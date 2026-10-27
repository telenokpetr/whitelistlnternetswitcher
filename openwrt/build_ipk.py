#!/usr/bin/env python3
"""Build a self-contained opkg archive using only the Python standard library."""
import argparse
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = '1.0.0-1'
NAME = 'luci-app-ded-obhod'
DEPENDS = 'libc, luci-base, uhttpd, curl, jq, coreutils-base64, xray-core, nftables-json, kmod-nft-tproxy'


def member(tar, name, data, mode=0o644):
    info = tarfile.TarInfo('./' + name.lstrip('/'))
    info.size, info.mode, info.mtime = len(data), mode, 0
    info.uid = info.gid = 0
    info.uname = info.gname = 'root'
    tar.addfile(info, io.BytesIO(data))


def archive(files, include_dirs=False):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz', format=tarfile.GNU_FORMAT) as tar:
        if include_dirs:
            directories = set()
            for name, _, _ in files:
                parent = Path(name).parent
                while parent.as_posix() not in ('', '.'):
                    directories.add(parent.as_posix())
                    parent = parent.parent
            for name in sorted(directories, key=lambda p: (p.count('/'), p)):
                info = tarfile.TarInfo('./' + name)
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 0
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                tar.addfile(info)
        for name, data, mode in files:
            member(tar, name, data, mode)
    return result.getvalue()


def build(output):
    files = []
    for source in sorted((ROOT / 'panel/www').rglob('*')):
        if source.is_file():
            rel = source.relative_to(ROOT / 'panel/www').as_posix()
            files.append(('www-panel/' + rel, source.read_bytes(), 0o755 if rel.startswith('cgi-bin/') else 0o644))
    mappings = {
        'panel/pc-daemon.sh': 'usr/bin/pc-daemon.sh',
        'panel/pc-panel.init': 'etc/init.d/pc-panel',
        'router/xray-sub-update.sh': 'usr/bin/xray-sub-update.sh',
        'router/xray-tp-guard.sh': 'usr/bin/xray-tp-guard.sh',
        'router/xray-vk.init': 'etc/init.d/xray-vk',
        'router/xray-tp.init': 'etc/init.d/xray-tp',
        'openwrt/ded-obhod-setup': 'usr/bin/ded-obhod-setup',
        'openwrt/luci-app-ded-obhod.json': 'usr/share/luci/menu.d/luci-app-ded-obhod.json',
        'openwrt/overview.js': 'www/luci-static/resources/view/ded-obhod/overview.js',
    }
    for source, target in mappings.items():
        files.append((target, (ROOT / source).read_bytes(), 0o644 if target.endswith(('.json', '.js')) else 0o755))
    control = (f'Package: {NAME}\nVersion: {VERSION}\nArchitecture: all\n'
               f'Depends: {DEPENDS}\nSection: luci\nPriority: optional\n'
               'Maintainer: Ded Obhod\nDescription: Ded Obhod router panel and LuCI service entry\n').encode()
    controls = [('control', control, 0o644), ('postinst', (ROOT / 'openwrt/postinst').read_bytes(), 0o755)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(archive([('debian-binary', b'2.0\n', 0o644),
                                ('data.tar.gz', archive(files, include_dirs=True), 0o644),
                                ('control.tar.gz', archive(controls), 0o644)]))
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / f'{NAME}_{VERSION}_all.ipk')
    build(parser.parse_args().output)
