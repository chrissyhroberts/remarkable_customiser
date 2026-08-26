from __future__ import annotations

import io
import json
import os
import posixpath
import shutil
import socket
import tempfile
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
    def __init__(self, config: DeviceConfig):
        self.config = config
        self.client: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None

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
        if self.config.password:
            kwargs["password"] = self.config.password
        if self.config.key_file:
            kwargs["key_filename"] = os.path.expanduser(self.config.key_file)
        try:
            client.connect(**kwargs)
        except (paramiko.SSHException, socket.error, OSError) as exc:
            raise DeviceError(f"SSH connection failed: {exc}") from exc
        self.client = client
        self.sftp = client.open_sftp()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.sftp is not None:
            self.sftp.close()
        if self.client is not None:
            self.client.close()

    def exec(self, command: str, *, check: bool = True) -> tuple[int, str, str]:
        if self.client is None:
            raise DeviceError("SSH session is not connected.")
        _stdin, stdout, stderr = self.client.exec_command(command)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if check and code != 0:
            raise DeviceError(f"Remote command failed ({code}): {command}\n{err.strip()}")
        return code, out, err

    def exists(self, path: str) -> bool:
        assert self.sftp is not None
        try:
            self.sftp.stat(path)
            return True
        except OSError:
            return False

    def read_json(self, path: str) -> dict[str, Any]:
        assert self.sftp is not None
        with self.sftp.open(path, "r") as handle:
            raw = handle.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def listdir(self, path: str) -> list[str]:
        assert self.sftp is not None
        return self.sftp.listdir(path)

    def download(self, remote_path: str, local_path: Path) -> None:
        assert self.sftp is not None
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(remote_path, str(local_path))

    def _replace_temp(self, temp_remote: str, remote_path: str) -> None:
        assert self.sftp is not None
        try:
            self.sftp.posix_rename(temp_remote, remote_path)
        except (AttributeError, OSError):
            if self.exists(remote_path):
                self.sftp.remove(remote_path)
            self.sftp.rename(temp_remote, remote_path)

    def upload_atomic(self, local_path: Path, remote_path: str) -> None:
        assert self.sftp is not None
        temp_remote = remote_path + ".rmpp-manager-upload"
        try:
            if self.exists(temp_remote):
                self.sftp.remove(temp_remote)
            self.sftp.put(str(local_path), temp_remote)
            self._replace_temp(temp_remote, remote_path)
        except Exception:
            try:
                if self.exists(temp_remote):
                    self.sftp.remove(temp_remote)
            except Exception:
                pass
            raise

    def upload_bytes_atomic(self, payload: bytes, remote_path: str) -> None:
        assert self.sftp is not None
        temp_remote = remote_path + ".rmpp-manager-upload"
        try:
            if self.exists(temp_remote):
                self.sftp.remove(temp_remote)
            with self.sftp.open(temp_remote, "wb") as handle:
                handle.write(payload)
            self._replace_temp(temp_remote, remote_path)
        except Exception:
            try:
                if self.exists(temp_remote):
                    self.sftp.remove(temp_remote)
            except Exception:
                pass
            raise


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


def build_plan(config: DeviceConfig, spec: DeploymentSpec) -> DeploymentPlan:
    with RemoteSession(config) as remote:
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
    plan = build_plan(config, spec)
    if not plan.actions:
        raise DeviceError("Nothing is selected for deployment.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = spec.backup_root.expanduser() / config.host / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    emit("Connecting and creating local backup...")
    with RemoteSession(config) as remote:
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
        emit("Remounting root filesystem read-write...")
        remote.exec("mount -o remount,rw /")

        try:
            if spec.suspend_screen is not None:
                emit("Uploading suspended.png...")
                remote.upload_atomic(spec.suspend_screen, REMOTE_SUSPENDED)

            if spec.carousel_mode == "blank":
                for name in plan.carousel_remote_files:
                    remote_path = posixpath.join(REMOTE_CAROUSEL, name)
                    backup_path = backup_dir / remote_path.lstrip("/")
                    emit(f"Blanking {name}...")
                    remote.upload_bytes_atomic(_make_blank_like(backup_path), remote_path)

            elif spec.carousel_mode == "custom":
                for asset in plan.carousel_custom_files:
                    emit(f"Uploading carousel image {asset.name}...")
                    remote.upload_atomic(asset, posixpath.join(REMOTE_CAROUSEL, asset.name))

            for asset in plan.template_assets:
                emit(f"Uploading template {asset.name}...")
                remote.upload_atomic(asset, posixpath.join(REMOTE_TEMPLATES, asset.name))

            if plan.registry_merge is not None:
                emit("Uploading merged templates.json...")
                payload = (json.dumps(plan.merged_registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                remote.upload_bytes_atomic(payload, REMOTE_REGISTRY)

            emit("Flushing writes...")
            remote.exec("sync")
            emit("Restarting xochitl...")
            remote.exec("systemctl restart xochitl")
        finally:
            emit("Attempting to return root filesystem to read-only...")
            remote.exec("mount -o remount,ro /", check=False)

    emit("Deployment complete.")
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

    with RemoteSession(config) as remote:
        emit("Remounting root filesystem read-write...")
        remote.exec("mount -o remount,rw /")
        try:
            for local_path in candidates:
                relative = local_path.relative_to(backup_dir)
                remote_path = "/" + str(relative).replace(os.sep, "/")
                emit(f"Restoring {remote_path}...")
                remote.upload_atomic(local_path, remote_path)
            remote.exec("sync")
            remote.exec("systemctl restart xochitl")
        finally:
            remote.exec("mount -o remount,ro /", check=False)
    emit("Backup restored.")
