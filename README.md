# reMarkable Customiser

A desktop GUI for customising reMarkable Paper Pro system templates, suspend screens, and sleep-carousel artwork over SSH.

The application is designed around the native reMarkable `.template` format and provides both a deployment interface and a visual template designer.

> **Warning**
>
> This application modifies files under `/usr/share/remarkable` as `root`. These modifications are not part of the officially supported reMarkable customisation workflow. Firmware updates may overwrite or change the files used by this application.

## Features

### Device customisation

The deployment interface can:

- connect to a reMarkable over SSH;
- authenticate with a password, SSH agent, default OpenSSH keys, or an explicitly selected private key;
- test the connection before making changes;
- replace the suspend screen at `/usr/share/remarkable/suspended.png`;
- blank the current sleep-carousel illustrations;
- upload custom carousel artwork;
- upload native `.template` files;
- fetch and merge the device's live `templates.json`;
- preview intended changes before deployment;
- create timestamped backups of affected files;
- restore files from a previous backup;
- restart `xochitl` after deployment.

The local suspend-screen image does **not** need to be named `suspended.png`. The application uploads the selected image to the correct fixed device path.

### Editable template designer

The template designer creates native reMarkable templates without requiring hand-written template JSON.

Supported elements include:

- horizontal lines;
- vertical lines;
- rectangles;
- circles;
- text;
- ruled lines;
- grids;
- dot grids;
- checklists.

Elements remain individually editable after they are added. You can select an element and change its position, dimensions, scaling, spacing, stroke, text, font preview settings, and other type-specific properties.

Elements can also be duplicated, deleted, moved up, and moved down.

The preview updates as the design changes.

## Template files and design files

A template created in the designer is saved in two forms:

```text
My_Template.template
My_Template.design.json
```

`My_Template.template` is the clean native template intended for deployment to the reMarkable.

`My_Template.design.json` stores the semantic design model used by the desktop editor so that individual elements can be reopened and edited later.

This separation is intentional: arbitrary native path data cannot always be reconstructed reliably into high-level objects such as grids or checklists.

If an existing `.template` file has no corresponding `.design.json`, it can still be loaded as a non-editable base layer and new editable elements can be added on top.

## Supported devices

The preview system currently includes dimensions for:

- reMarkable Paper Pro;
- reMarkable 2.

The deployment workflow has primarily been developed and tested against reMarkable Paper Pro.

## Installation

Python 3.11 or later is recommended.

Clone the repository and install it in a virtual environment:

```bash
git clone https://github.com/chrissyhroberts/remarkable_customiser.git
cd remarkable_customiser

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .
```

Run the application with:

```bash
python -m rmpp_manager
```

or:

```bash
rmpp-manager
```

On Windows, activate the virtual environment with:

```text
.venv\Scripts\activate
```

## SSH authentication

The device tab supports password authentication, SSH agent authentication, keys in the usual OpenSSH locations, and an explicit private-key file.

The password field is masked and is not intended to be persisted in application settings.

The device host defaults to:

```text
root@192.168.86.87
```

Change the IP address in the GUI if your reMarkable uses a different address.

## Deployment architecture

The reMarkable root filesystem is normally mounted read-only. Deployment therefore uses deliberately separate SSH connections for the different stages of the operation.

The workflow is:

```text
1. Connect and inspect the device
2. Build the deployment plan
3. Back up affected files
4. Disconnect
5. Connect and remount / read-write
6. Verify the mount and shell write access
7. Disconnect
8. Establish a brand-new SSH connection
9. Upload and verify files
10. sync
11. restart xochitl
12. Disconnect
13. Establish a cleanup connection
14. Remount / read-only
15. Verify the final mount state
```

The reconnect after remounting is deliberate. It avoids reusing the pre-remount transport for system-file writes.

### File transfer

SFTP is used for reading and backing up files.

Writes use a normal SSH execution channel. Each upload is written to a temporary file, checked for the expected byte count, and then renamed into place.

The deployment log records connection phases, root mount state, write-access checks, upload start and completion, byte counts, transfer progress, temporary-file verification, rename operations, `sync`, `xochitl` restart, and the final read-only remount.

This is deliberately verbose so failures on different reMarkable firmware versions can be diagnosed.

## Suspend screen

Select any PNG in the GUI.

It will be deployed as:

```text
/usr/share/remarkable/suspended.png
```

The application backs up the existing file before replacing it.

## Sleep carousel

The application can either leave the carousel unchanged, upload custom carousel files, or blank the currently installed sleep illustrations.

Blank-carousel mode discovers files matching:

```text
/usr/share/remarkable/carousel/sleep_Illustration_*.png
```

Rather than assuming a fixed image size, it creates blank replacements using the dimensions of the files currently installed on the device.

## Templates

Native template files are installed under:

```text
/usr/share/remarkable/templates
```

The registry is:

```text
/usr/share/remarkable/templates/templates.json
```

The application fetches the **live** registry from the device before deployment. It does not overwrite it with a bundled copy.

### Registry merge behaviour

Template entries are matched using filename and orientation.

The merge logic is designed to preserve device metadata and unknown fields:

1. An identical existing entry is left unchanged.
2. A matching filename/orientation with different metadata is treated as a conflict.
3. The same display name with another filename or orientation produces a warning but does not necessarily prevent insertion.
4. A genuinely new filename/orientation is appended.

Conflicting entries are kept by default unless replacement is explicitly requested.

## Local library and backups

A typical local library looks like:

```text
~/Documents/reMarkable Templates/
├── templates/
│   ├── My_Template.template
│   ├── My_Template.design.json
│   └── templates.json
└── backups/
    └── 192.168.86.87/
        └── YYYY-MM-DD_HHMMSS/
            └── usr/share/remarkable/...
```

Backups preserve the remote path beneath the timestamped directory.

## Safety

The application takes several precautions before modifying the device:

- fetches the live template registry;
- validates registry JSON;
- previews the deployment plan;
- creates local backups;
- remounts `/` read-write only for the deployment phase;
- verifies shell write access after remounting;
- writes via temporary files;
- verifies uploaded byte counts;
- runs `sync`;
- restarts `xochitl`;
- attempts a fresh cleanup connection even after failures;
- remounts `/` read-only afterwards.

These measures reduce risk, but system modifications remain unsupported by reMarkable.

A firmware update may overwrite customisations, rename system files, change template behaviour, alter the carousel structure, or change the SSH environment.

After a firmware update, use **Test connection** and **Preview changes** before deploying anything.

## Development

Install the development dependencies:

```bash
python -m pip install -e '.[dev]'
```

Run the tests:

```bash
python -m pytest -q
```

The main implementation is separated into components including:

```text
src/rmpp_manager/
├── main_window.py
├── device.py
├── registry.py
├── template_engine.py
├── design_model.py
└── editor.py
```

The separation between Qt UI code, device operations, registry merging, rendering, and the semantic design model is intentional so the more destructive operations can be tested independently.

## Desktop builds

GitHub Actions builds native desktop packages for:

- macOS Apple Silicon;
- Windows x64.

PyInstaller is used to package the application.

The macOS build is ad-hoc signed but is not currently Developer ID signed or notarised. macOS may therefore display a Gatekeeper warning for downloaded releases.

The Windows executable is not currently Authenticode-signed, so Windows SmartScreen may warn on first launch.

## Releases

Version information is defined in `pyproject.toml`.

Tagged releases use tags of the form:

```text
v0.2.3
```

A tagged GitHub Actions run builds the macOS and Windows packages and attaches them to the corresponding GitHub Release.

## Licence

See [`LICENSE`](LICENSE).

## Acknowledgements

The project grew out of work on native reMarkable Paper Pro template customisation and the template structures explored in:

[`chrissyhroberts/remarkable_2_and_paper_pro_custom_templates`](https://github.com/chrissyhroberts/remarkable_2_and_paper_pro_custom_templates)
