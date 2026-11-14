# MIB Explorer

A small desktop application for browsing SNMP OID trees from local MIB and SMI files. It uses PySMI to parse SMIv1/SMIv2 definitions and Tkinter/ttk for the interface. It does not send SNMP requests.

## Requirements

- Python 3.9 or newer
- Tkinter (typically included with Python; some Linux distributions package it separately)
- The Python dependencies in `requirements.txt`

Install dependencies into the project virtual environment:

```sh
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

From the project directory:

```sh
. .venv/bin/activate
python -m mib_explorer
```

Choose one or more `.mib`/`.smi` files, or a folder containing MIB files. `.txt` and `.my` MIB repositories are also accepted. PySMI resolves imported modules using the selected files' directories and recursively searched folders. For local imports to resolve, keep dependent MIB files in those locations; this application does not fetch MIBs from the network.

Filenames should conventionally match their MIB module names (case-insensitive), as PySMI resolves source modules by module name. Parser/compiler warnings appear below the OID browser. Search matches names, descriptions, metadata, or numeric OIDs.

## Tests

```sh
. .venv/bin/activate
python -m unittest discover -s tests -v
```
