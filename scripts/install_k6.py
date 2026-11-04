"""Install one checksummed official native k6 binary under ignored local data."""
import hashlib
import io
import json
import platform
import tarfile
import urllib.request
import zipfile
from download_esci import ROOT
from prepare_esci_splits import file_digest


def install():
    policy = json.loads((ROOT / 'config/api-load-v1.json').read_text())
    key = f'{platform.system()}-{platform.machine()}'
    spec = policy['k6_archives'][key]
    folder = ROOT / 'data/tools' / f"k6-v{policy['k6_version']}-{key}"
    binary, manifest = folder / 'k6', folder / 'manifest.json'
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if saved['archive'] != spec or file_digest(binary) != saved['binary_sha256']:
            raise ValueError('Installed tool differs from its pinned manifest')
        return binary
    if binary.exists(): raise FileExistsError('Unverified binary exists; preserve and inspect it')
    url = f"https://github.com/grafana/k6/releases/download/v{policy['k6_version']}/{spec['name']}"
    with urllib.request.urlopen(url, timeout=60) as response: archive = response.read()
    if hashlib.sha256(archive).hexdigest() != spec['sha256']: raise ValueError('Official archive checksum mismatch')
    if spec['name'].endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            names = [n for n in package.namelist() if n.endswith('/k6')]
            if len(names) != 1: raise ValueError('Ambiguous executable member')
            content = package.read(names[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
            members = [m for m in package.getmembers() if m.isfile() and m.name.endswith('/k6')]
            if len(members) != 1: raise ValueError('Ambiguous executable member')
            content = package.extractfile(members[0]).read()
    folder.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(content); binary.chmod(0o755)
    manifest.write_text(json.dumps({'archive': spec, 'url': url, 'binary_sha256': file_digest(binary)}, indent=2) + '\n')
    return binary


if __name__ == '__main__': print(install())
