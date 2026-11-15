# MIB Explorer

A small desktop application for browsing SNMP OID trees from local MIB and SMI files. It uses PySMI to parse SMIv1/SMIv2 definitions, PySNMP for read-only SNMPv2c subtree walks, and Tkinter/ttk for the interface.

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

You can optionally pass one or more files or directories to load immediately at startup:

```sh
python -m mib_explorer ./mibs
python -m mib_explorer ./mibs ./vendor/EXTRA-MIB
```

Directories are searched recursively. Without paths, choose one or more `.mib`/`.smi` files, or a folder in the GUI. `.txt` and `.my` MIB repositories are also accepted. PySMI resolves imported modules using the selected files' directories and recursively searched folders. For local imports to resolve, keep dependent MIB files in those locations; this application does not fetch MIBs from the network.

Filenames should conventionally match their MIB module names (case-insensitive), as PySMI resolves source modules by module name. Parser/compiler warnings appear below the OID browser. Search accepts case-insensitive Python regular expressions and matches names, descriptions, metadata, or numeric OIDs.

Enter the agent hostname, UDP port, and SNMPv2c community in the **SNMP target** fields; the community defaults to `public`, is displayed as plain text, and all values remain available for subsequent queries during the session. Right-click an OID node and choose **Get subtree** to walk that OID on the configured agent. Returned OIDs and values appear in a new tab; the MIB tree and definition details remain together in the first **MIB Browser** tab. SNMPv2c does not encrypt community strings or responses; use it only on trusted networks and agents you are authorized to query. This feature performs read-only walks and does not discover or scan hosts.

## Tests

```sh
. .venv/bin/activate
python -m unittest discover -s tests -v
```
