#!/usr/bin/env python3
"""Build only. Never installs, starts launchd, or changes the machine's network."""
import argparse
import ipaddress
import json
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--subnet',required=True,help='Home LAN IPv4 CIDR, for example 192.168.50.0/24')
parser.add_argument('--interface',default='en0',help='Real hardware interface; utun is not supported')
parser.add_argument('--python',default=shutil.which('python3'),dest='python_path')
parser.add_argument('--output',default=str(ROOT/'build/NS2下载加速.app'))
args=parser.parse_args()
if platform.system()!='Darwin':parser.error('Building the native app requires macOS and Xcode Command Line Tools.')
network=ipaddress.ip_network(args.subnet,strict=False)
if network.version!=4 or not network.is_private or network.prefixlen<16:parser.error('Use a private IPv4 subnet /16 or smaller.')
if not re.fullmatch(r'en\d+',args.interface):parser.error('Use a real enN network interface, not a VPN interface.')
if not args.python_path or not Path(args.python_path).is_absolute() or not Path(args.python_path).is_file():parser.error('--python must be an existing absolute Python 3 path.')
python_path=str(Path(args.python_path).absolute())
subprocess.run([python_path,'-c','import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'],check=True)
arch=platform.machine()
if arch not in ('arm64','x86_64'):parser.error('Unsupported CPU architecture: '+arch)
output=Path(args.output).absolute()
if output.exists():parser.error('Output exists; choose another --output or move the previous build aside.')
mac=output/'Contents/MacOS';res=output/'Contents/Resources';mac.mkdir(parents=True);res.mkdir(parents=True)
for source in (ROOT/'src/backend').glob('*.py'):shutil.copy2(source,res/source.name)
control=res/'control.py';control.write_text(control.read_text().replace("PYTHON='/opt/homebrew/bin/python3'",'PYTHON='+repr(python_path)))
source=(ROOT/'src/app/NS2Direct.swift').read_text()
source=source.replace('Configuration(interface: "en0"','Configuration(interface: '+json.dumps(args.interface))
source=source.replace('allowed_subnet: "192.168.50.0/24"','allowed_subnet: '+json.dumps(str(network)))
source=source.replace('URL(fileURLWithPath: "/opt/homebrew/bin/python3")','URL(fileURLWithPath: '+json.dumps(python_path,ensure_ascii=False)+')')
with tempfile.TemporaryDirectory(prefix='ns2-build-') as temp:
 swift=Path(temp)/'NS2Direct.swift';swift.write_text(source)
 subprocess.run(['xcrun','swiftc','-parse-as-library','-swift-version','5','-target',arch+'-apple-macosx14.0','-O','-framework','SwiftUI','-framework','AppKit','-o',str(mac/'NS2Direct'),str(swift)],check=True)
shutil.copy2(ROOT/'LICENSE',res/'LICENSE');shutil.copy2(ROOT/'assets/AppIcon.icns',res/'AppIcon.icns')
info={'CFBundleDevelopmentRegion':'zh_CN','CFBundleIdentifier':'com.local.ns2direct','CFBundleName':'NS2下载加速','CFBundleDisplayName':'NS2下载加速','CFBundleExecutable':'NS2Direct','CFBundlePackageType':'APPL','CFBundleShortVersionString':'1.4.1','CFBundleVersion':'141','CFBundleIconFile':'AppIcon','LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,'LSUIElement':True,'NSLocalNetworkUsageDescription':'为同一局域网的 Nintendo Switch 提供下载转发及连接诊断。'}
(output/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(output)],check=True)
subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(output)],check=True)
print('Built:',output)
print('Nothing installed or started. Read README.md before first launch or upgrade.')
