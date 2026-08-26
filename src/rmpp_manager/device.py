from __future__ import annotations

import io
import json
import os
import posixpath
import shlex
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import paramiko
from PIL import Image

from .registry import RegistryMerge, discover_local_templates, merge_registry


REMOTE_ROOT = "/usr/share/remarkable"
REMOTE_TEMPLATES = f"{REMOTE_ROOT}/templates"
REMOTE_REGISTRY = f"{REMOTE_TEMPLATES}/templates.json"
REMOTE_SUSPENDED = f"{REMOTE_ROOT}/suspended.png"
REMOTE_CAROUSEL = f"{REMOTE_ROOT}/carousel"


@dataclass(slots=True)
class DeviceConfig:
    host: str = "192.168.86.87"
    user: str = "root"
    password: str = ""
    key_file: str = ""
    port: int = 22
    timeout: float = 8.0


@dataclass(slots=True)
class DeploymentSpec:
    suspend_screen: Path | None = None
    carousel_mode: str = "blank"  # leave | blank | custom
    carousel_folder: Path | None = None
    template_folder: Path | None = None
    replace_registry_conflicts: bool = False
    backup_root: Path = Path.home() / "Documents" / "reMarkable Templates" / "backups"


@dataclass(slots=True)
class DeploymentPlan:
    remote_registry: dict[str, Any]
    merged_registry: dict[str, Any]
    registry_merge: RegistryMerge | None
    template_assets: list[Path] = field(default_factory=list)
    carousel_remote_files: list[str] = field(default_factory=list)
    carousel_custom_files: list[Path] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DeviceError(RuntimeError):
    pass


class RemoteSession:
    def __init__(
        self,
        config: DeviceConfig,
        *,
        log: Callable[[str], None] | None = None,
        label: str = "session",
    ):
        self.config = config
        self.client: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self.log = log or (lambda _message: None)
        self.label = label

    def _debug(self, message: str) -> None:
        self.log(f"[SSH:{self.label}] {message}")

    def __enter__(self) -> "RemoteSession":
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self.config.host,
            "port": self.config.port,
            "username": self.config.user,
            "timeout": self.config.timeout,
            "auth_timeout": self.config.timeout,
            "banner_timeout": self.config.timeout,
            "look_for_keys": True,
            "allow_agent": True,
        }
        auth = []
        if getattr(self.config, "password", ""):
            kwargs["password"] = self.config.password
            auth.append("password")
        if self.config.key_file:
            kwargs["key_filename"] = os.path.expanduser(self.config.key_file)
            auth.append("explicit-key")
        auth.append("agent/default-keys")
        self._debug(
            f"CONNECT {self.config.user}@{self.config.host}:{self.config.port} "
            f"auth={','.join(auth)}"
        )
        try:
            client.connect(**kwargs)
        except (paramiko.SSHException, socket.error, OSError) as exc:
            self._debug(f"CONNECT FAILED {type(exc).__name__}: {exc}")
            raise DeviceError(f"SSH connection failed: {exc}") from exc
        self.client = client
        transport = client.get_transport()
        self._debug(f"CONNECTED transport_active={bool(transport and transport.is_active())}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.sftp is not None:
            self._debug("closing SFTP")
            self.sftp.close()
            self.sftp = None
        if self.client is not None:
            self._debug("closing SSH")
            self.client.close()
            self.client = None

    def _ensure_sftp(self) -> paramiko.SFTPClient:
        if self.client is None:
            raise DeviceError("SSH session is not connected.")
        if self.sftp is None:
            self._debug("opening SFTP for read/backup")
            self.sftp = self.client.open_sftp()
            self._debug("SFTP opened")
        return self.sftp

    def _wait_exit(self, channel, timeout: float, description: str) -> int:
        deadline = time.monotonic() + timeout
        while not channel.exit_status_ready():
            if time.monotonic() >= deadline:
                raise DeviceError(f"Timed out waiting for: {description}")
            time.sleep(0.05)
        return channel.recv_exit_status()

    def exec(self, command: str, *, check: bool = True, timeout: float = 45.0) -> tuple[int, str, str]:
        if self.client is None:
            raise DeviceError("SSH session is not connected.")
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise DeviceError("SSH transport is not active.")
        self._debug(f"EXEC {command}")
        channel = transport.open_session(timeout=min(timeout, 15.0))
        channel.settimeout(timeout)
        try:
            channel.exec_command(command)
            code = self._wait_exit(channel, timeout, command)
            out = b""
            err = b""
            while channel.recv_ready():
                out += channel.recv(65536)
            while channel.recv_stderr_ready():
                err += channel.recv_stderr(65536)
            out_text = out.decode('utf-8', errors='replace')
            err_text = err.decode('utf-8', errors='replace')
            detail = f"EXEC exit={code}"
            if out_text.strip():
                detail += f" stdout={out_text.strip()[:400]!r}"
            if err_text.strip():
                detail += f" stderr={err_text.strip()[:400]!r}"
            self._debug(detail)
            if check and code != 0:
                raise DeviceError(f"Remote command failed ({code}): {command}\n{err_text.strip()}")
            return code, out_text, err_text
        finally:
            channel.close()

    def mount_state(self) -> str:
        _code, out, _err = self.exec(
            "mount | grep ' on / ' || grep ' / ' /proc/mounts | head -1",
            check=False,
        )
        state = out.strip() or '(unavailable)'
        self._debug(f"MOUNT {state}")
        return state

    def verify_write_access(self) -> None:
        marker = shlex.quote(f"{REMOTE_ROOT}/.rmpp-manager-write-test")
        self._debug("WRITE TEST in /usr/share/remarkable")
        self.exec(f"printf test > {marker} && test -s {marker} && rm -f {marker}")
        self._debug("WRITE TEST passed")

    def exists(self, path: str) -> bool:
        sftp = self._ensure_sftp()
        try:
            sftp.stat(path)
            return True
        except OSError:
            return False

    def read_json(self, path: str) -> dict[str, Any]:
        sftp = self._ensure_sftp()
        self._debug(f"SFTP READ {path}")
        with sftp.open(path, 'r') as handle:
            raw = handle.read()
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        return json.loads(raw)

    def listdir(self, path: str) -> list[str]:
        self._debug(f"SFTP LIST {path}")
        return self._ensure_sftp().listdir(path)

    def download(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._debug(f"SFTP BACKUP {remote_path} -> {local_path}")
        self._ensure_sftp().get(remote_path, str(local_path))
        self._debug(f"SFTP BACKUP done bytes={local_path.stat().st_size}")

    def _upload_stream_atomic(self, source, remote_path: str, expected_size: int) -> None:
        if self.client is None:
            raise DeviceError("SSH session is not connected.")
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise DeviceError("SSH transport is not active.")

        temp_remote = remote_path + '.rmpp-manager-upload'
        qtemp = shlex.quote(temp_remote)
        qtarget = shlex.quote(remote_path)
        self._debug(f"UPLOAD start local_bytes={expected_size} target={remote_path}")
        self.exec(f"rm -f {qtemp}", check=False)

        channel = transport.open_session(timeout=15.0)
        channel.settimeout(30.0)
        sent = 0
        next_report = 0.25
        try:
            self._debug(f"UPLOAD writer: cat > {temp_remote}")
            channel.exec_command(f"cat > {qtemp}")
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                channel.sendall(chunk)
                sent += len(chunk)
                if expected_size and sent / expected_size >= next_report:
                    self._debug(f"UPLOAD progress {sent}/{expected_size} bytes")
                    next_report += 0.25
            self._debug(f"UPLOAD payload sent={sent}; sending EOF")
            channel.shutdown_write()
            code = self._wait_exit(channel, 30.0, f"cat > {temp_remote}")
            err = b""
            while channel.recv_stderr_ready():
                err += channel.recv_stderr(65536)
            err_text = err.decode('utf-8', errors='replace').strip()
            self._debug(f"UPLOAD writer exit={code} stderr={err_text!r}")
            if code != 0:
                raise DeviceError(f"Upload failed for {remote_path}: {err_text}")

            vcode, out, verr = self.exec(f"wc -c < {qtemp}", check=False)
            if vcode != 0:
                raise DeviceError(f"Could not verify upload: {verr.strip()}")
            remote_size = int(out.strip())
            self._debug(f"UPLOAD verify local={expected_size} remote={remote_size}")
            if remote_size != expected_size:
                raise DeviceError(
                    f"Upload size mismatch for {remote_path}: local={expected_size} remote={remote_size}"
                )
            self.exec(f"mv -f {qtemp} {qtarget}")
            self._debug(f"UPLOAD complete {remote_path}")
        except Exception as exc:
            self._debug(f"UPLOAD FAILED {type(exc).__name__}: {exc}")
            self.exec(f"rm -f {qtemp}", check=False)
            raise
        finally:
            channel.close()

    def upload_atomic(self, local_path: Path, remote_path: str) -> None:
        local_path = Path(local_path)
        with local_path.open('rb') as source:
            self._upload_stream_atomic(source, remote_path, local_path.stat().st_size)

    def upload_bytes_atomic(self, payload: bytes, remote_path: str) -> None:
        self._upload_stream_atomic(io.BytesIO(payload), remote_path, len(payload))

def test_connection(config: DeviceConfig) -> str:
    with RemoteSession(config) as remote:
        _code, output, _err = remote.exec("uname -a; printf '\\n'; cat /etc/os-release 2>/dev/null | head -5", check=False)
        return output.strip() or "Connected."


def _carousel_files(remote: RemoteSession) -> list[str]:
    if not remote.exists(REMOTE_CAROUSEL):
        return []
    return sorted(
        name
        for name in remote.listdir(REMOTE_CAROUSEL)
        if name.lower().endswith(".png") and name.startswith("sleep_Illustration_")
    )


def build_plan(
    config: DeviceConfig,
    spec: DeploymentSpec,
    *,
    log: Callable[[str], None] | None = None,
) -> DeploymentPlan:
    with RemoteSession(config, log=log, label='plan') as remote:
        if not remote.exists(REMOTE_REGISTRY):
            raise DeviceError(f"Template registry not found: {REMOTE_REGISTRY}")
        registry = remote.read_json(REMOTE_REGISTRY)
        plan = DeploymentPlan(remote_registry=registry, merged_registry=registry, registry_merge=None)

        if spec.suspend_screen is not None:
            if not spec.suspend_screen.is_file():
                raise DeviceError(f"Suspended screen does not exist: {spec.suspend_screen}")
            plan.actions.append(f"Replace {REMOTE_SUSPENDED} with {spec.suspend_screen.name}")

        carousel_files = _carousel_files(remote)
        plan.carousel_remote_files = carousel_files
        if spec.carousel_mode == "blank":
            if carousel_files:
                plan.actions.append(f"Blank {len(carousel_files)} sleep carousel image(s)")
            else:
                plan.warnings.append("No sleep_Illustration_*.png files were found in the carousel directory.")
        elif spec.carousel_mode == "custom":
            if spec.carousel_folder is None or not spec.carousel_folder.is_dir():
                raise DeviceError("Custom carousel mode selected but no valid folder was provided.")
            custom = sorted(spec.carousel_folder.glob("*.png"))
            if not custom:
                raise DeviceError("The custom carousel folder contains no PNG files.")
            plan.carousel_custom_files = custom
            plan.actions.append(f"Upload {len(custom)} custom carousel image(s)")
        elif spec.carousel_mode != "leave":
            raise DeviceError(f"Unknown carousel mode: {spec.carousel_mode}")

        if spec.template_folder is not None:
            assets, entries = discover_local_templates(spec.template_folder)
            if not assets:
                plan.warnings.append("The selected template folder contains no .template files.")
            merge = merge_registry(
                registry,
                entries,
                replace_conflicts=spec.replace_registry_conflicts,
            )
            plan.registry_merge = merge
            plan.merged_registry = merge.merged
            if merge.conflicts and not spec.replace_registry_conflicts:
                conflict_filenames = {
                    str(local.get("filename", ""))
                    for _remote, local in merge.conflicts
                }
                plan.template_assets = [
                    asset for asset in assets if asset.stem not in conflict_filenames
                ]
                skipped = len(assets) - len(plan.template_assets)
                if skipped:
                    plan.warnings.append(
                        f"Skipped {skipped} local template file(s) whose registry entries conflict "
                        "with the live device. Enable conflict replacement to upload them."
                    )
            else:
                plan.template_assets = assets
            plan.actions.append(f"Upload {len(plan.template_assets)} native template file(s)")
            plan.actions.append(f"Add {len(merge.added)} registry entr{'y' if len(merge.added) == 1 else 'ies'}")
            if merge.unchanged:
                plan.actions.append(f"Leave {len(merge.unchanged)} already-identical registry entr{'y' if len(merge.unchanged) == 1 else 'ies'} unchanged")
            if merge.conflicts:
                behaviour = "replace" if spec.replace_registry_conflicts else "keep device version of"
                plan.warnings.append(f"{len(merge.conflicts)} registry conflict(s): deployment will {behaviour} those entries.")
            if merge.name_collisions:
                plan.warnings.append(f"{len(merge.name_collisions)} name collision(s) use different filenames/orientations.")

        if not plan.actions:
            plan.warnings.append("Nothing is selected for deployment.")
        return plan


def _backup_one(remote: RemoteSession, remote_path: str, backup_dir: Path) -> None:
    if not remote.exists(remote_path):
        return
    relative = remote_path.lstrip("/")
    remote.download(remote_path, backup_dir / relative)


def _make_blank_like(path: Path) -> bytes:
    with Image.open(path) as image:
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        fill = (255, 255, 255, 0) if mode == "RGBA" else (255, 255, 255)
        blank = Image.new(mode, image.size, fill)
        buffer = io.BytesIO()
        blank.save(buffer, format="PNG")
        return buffer.getvalue()


def deploy(
    config: DeviceConfig,
    spec: DeploymentSpec,
    *,
    log: Callable[[str], None] | None = None,
) -> Path:
    emit = log or (lambda _message: None)
    emit("========== DEPLOY START ==========")
    emit(
        f"Target {config.user}@{config.host}:{config.port}; "
        f"password={'yes' if getattr(config, 'password', '') else 'no'}; "
        f"explicit_key={'yes' if config.key_file else 'no'}"
    )

    emit("PHASE 1/5 plan/read")
    plan = build_plan(config, spec, log=emit)
    if not plan.actions:
        raise DeviceError("Nothing is selected for deployment.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = spec.backup_root.expanduser() / config.host / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    emit("PHASE 2/5 backup on read connection")
    with RemoteSession(config, log=emit, label="backup") as remote:
        remote.mount_state()
        _backup_one(remote, REMOTE_REGISTRY, backup_dir)
        if spec.suspend_screen is not None:
            _backup_one(remote, REMOTE_SUSPENDED, backup_dir)
        for name in plan.carousel_remote_files:
            _backup_one(remote, posixpath.join(REMOTE_CAROUSEL, name), backup_dir)
        for asset in plan.carousel_custom_files:
            _backup_one(remote, posixpath.join(REMOTE_CAROUSEL, asset.name), backup_dir)
        for asset in plan.template_assets:
            _backup_one(remote, posixpath.join(REMOTE_TEMPLATES, asset.name), backup_dir)
    emit(f"Backup saved to {backup_dir}")
    emit("Backup connection CLOSED")

    emit("PHASE 3/5 NEW connection: remount RW")
    with RemoteSession(config, log=emit, label="remount-rw") as remote:
        emit("Before remount:")
        remote.mount_state()
        remote.exec("mount -o remount,rw /")
        emit("After remount:")
        remote.mount_state()
        remote.verify_write_access()
    emit("RW-remount connection CLOSED")

    upload_succeeded = False
    cleanup_error: Exception | None = None
    try:
        emit("PHASE 4/5 BRAND-NEW connection after remount: uploads")
        with RemoteSession(config, log=emit, label="upload") as remote:
            remote.mount_state()
            remote.verify_write_access()

            if spec.suspend_screen is not None:
                emit(f"Uploading suspended.png from {spec.suspend_screen}")
                remote.upload_atomic(spec.suspend_screen, REMOTE_SUSPENDED)

            if spec.carousel_mode == "blank":
                for name in plan.carousel_remote_files:
                    remote_path = posixpath.join(REMOTE_CAROUSEL, name)
                    backup_path = backup_dir / remote_path.lstrip("/")
                    emit(f"Blanking {name}")
                    remote.upload_bytes_atomic(_make_blank_like(backup_path), remote_path)
            elif spec.carousel_mode == "custom":
                for asset in plan.carousel_custom_files:
                    emit(f"Uploading carousel {asset.name}")
                    remote.upload_atomic(asset, posixpath.join(REMOTE_CAROUSEL, asset.name))

            for asset in plan.template_assets:
                emit(f"Uploading template {asset.name}")
                remote.upload_atomic(asset, posixpath.join(REMOTE_TEMPLATES, asset.name))

            if plan.registry_merge is not None:
                emit("Uploading merged templates.json")
                payload = (json.dumps(plan.merged_registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                remote.upload_bytes_atomic(payload, REMOTE_REGISTRY)

            emit("sync")
            remote.exec("sync", timeout=60.0)
            emit("restart xochitl")
            remote.exec("systemctl restart xochitl", timeout=60.0)
            emit("upload phase complete")
        upload_succeeded = True
    finally:
        emit("PHASE 5/5 BRAND-NEW cleanup connection: remount RO")
        try:
            with RemoteSession(config, log=emit, label="cleanup-ro") as remote:
                remote.mount_state()
                remote.exec("mount -o remount,ro /", check=False)
                remote.mount_state()
        except Exception as exc:
            cleanup_error = exc
            emit(f"WARNING cleanup failed: {type(exc).__name__}: {exc}")

    if cleanup_error is not None and upload_succeeded:
        raise DeviceError(
            "Writes completed, but read-only remount could not be verified: "
            f"{cleanup_error}"
        )
    emit("========== DEPLOY COMPLETE ==========")
    return backup_dir

def restore_backup(
    config: DeviceConfig,
    backup_dir: Path,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    emit = log or (lambda _message: None)
    backup_dir = backup_dir.expanduser().resolve()
    if not backup_dir.is_dir():
        raise DeviceError(f"Backup directory does not exist: {backup_dir}")
    candidates = [p for p in backup_dir.rglob("*") if p.is_file()]
    if not candidates:
        raise DeviceError("Backup directory contains no files.")

    emit("========== RESTORE START ==========")
    emit("RESTORE 1/3 NEW connection: remount RW")
    with RemoteSession(config, log=emit, label="restore-rw") as remote:
        remote.mount_state()
        remote.exec("mount -o remount,rw /")
        remote.mount_state()
        remote.verify_write_access()

    restore_succeeded = False
    cleanup_error: Exception | None = None
    try:
        emit("RESTORE 2/3 BRAND-NEW connection: uploads")
        with RemoteSession(config, log=emit, label="restore-upload") as remote:
            remote.mount_state()
            remote.verify_write_access()
            for local_path in candidates:
                relative = local_path.relative_to(backup_dir)
                remote_path = "/" + str(relative).replace(os.sep, "/")
                emit(f"Restoring {remote_path}")
                remote.upload_atomic(local_path, remote_path)
            remote.exec("sync", timeout=60.0)
            remote.exec("systemctl restart xochitl", timeout=60.0)
        restore_succeeded = True
    finally:
        emit("RESTORE 3/3 BRAND-NEW cleanup connection: remount RO")
        try:
            with RemoteSession(config, log=emit, label="restore-ro") as remote:
                remote.mount_state()
                remote.exec("mount -o remount,ro /", check=False)
                remote.mount_state()
        except Exception as exc:
            cleanup_error = exc
            emit(f"WARNING restore cleanup failed: {type(exc).__name__}: {exc}")

    if cleanup_error is not None and restore_succeeded:
        raise DeviceError(
            "Restore completed, but read-only remount could not be verified: "
            f"{cleanup_error}"
        )
    emit("========== RESTORE COMPLETE ==========")

