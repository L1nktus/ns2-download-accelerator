# NS2下载加速 — NS2 Download Accelerator

[简体中文](README.md) | **English**

A LAN HTTP download proxy for Nintendo Switch / Switch 2 running on a Mac, with a native menu bar app, live download speed monitoring, and configurable recovery from sustained slow downloads.

## AI-assisted development and maintenance

The account owner defines the requirements and primarily uses AI (Codex) to help write, modify, test, and maintain this project. **The account owner has no programming knowledge and cannot independently review the entire implementation.** The source is published to preserve the work, share practical experience, and help other developers and AI tools improve it.

This is a personal-use project. Professional human auditing, ongoing maintenance, and compatibility with every device are not guaranteed. Documentation distinguishes automated tests, simulations, and real-device experience. Issues and pull requests are welcome. Viewing, downloading, or forking this public repository does not grant write access; proposed changes are merged at the maintainer's discretion.

## What it does

The Switch downloads through a Mac on the same local network. Outgoing connections are bound to a selected physical network interface. The program does not configure proxy nodes or fall back to the system proxy. A VPN's enforced routing policies may still affect the actual path: verify behavior on your own Mac rather than assuming interface binding bypasses every VPN configuration.

- Native controls for the service, Switch proxy settings, connection logs, and download speed. At least one entry point—the menu bar or Dock—must remain enabled.
- Default slow-download recovery: **below 100 KB/s for 60 seconds → stop forwarding for 15 seconds → restart → observe for up to 180 seconds**. All four settings are editable. An observation timeout only records the result; it does not itself trigger another restart. At most two automatic recoveries occur per rolling 30 minutes. KB/s uses decimal bytes, not bits.
- Closing the window keeps the service running; quitting the app stops it. Turning off the display is supported, but system sleep interrupts forwarding. The app does not manage display wakefulness or prevent sleep.

## Build and use

Requirements: macOS 14+, Xcode Command Line Tools, and Python 3.10+. Apple Silicon has been tested; the Intel build option has not been tested on Intel hardware. No third-party Python packages are required.

First identify your physical interface, local IPv4 subnet, and Python executable. The following subnet and Python path are examples; replace them with your own values:

```sh
python3 tools/test.py
python3 tools/build.py --interface en0 --subnet 192.168.50.0/24 --python /opt/homebrew/bin/python3
```

Copy `build/NS2下载加速.app` to Applications and open it. The build script only builds the app; it does not install it or change network settings. Once the app reports a usable network, enable manual proxy settings on the Switch and enter the displayed server address and port (8888 by default). Authentication is not required; leave DNS automatic.

**Upgrading an existing installation:** quit the old app and back up its runtime directory first. The app currently copies only missing backend files. Update the four backend Python files in the current user's `Library/Application Support/NS2Proxy` from the new app's resources, while preserving your own `config.json`. Otherwise, the old backend may remain in use.

## Validation and limitations

At the initial public-source release, 40 backend unit/local-socket tests passed. An isolated recovery test verified a roughly 15-second forwarding outage and cancellation of a pending restart. Menu state and Dock hiding were checked on the original Mac.

The user has observed that restarting the service lets the Switch resume downloads automatically. Actual game-download speed readings in the latest UI, compatibility across devices, and the long-term effectiveness of automatic recovery still need further validation. Reconnecting is not guaranteed to improve speed every time.

Additional documentation:

- [AI handoff notes (Chinese)](AI_HANDOFF.md)
- [Technical notes (Chinese)](docs/technical-notes.md)
- [Maintenance policy (Chinese)](MAINTENANCE.md)
- [Contribution guidelines (Chinese)](CONTRIBUTING.md)

## Upstream and license

Derived from `proxy_accel.py` in [kkksssmmmm-nuoyier/ns2-dns-accel](https://github.com/kkksssmmmm-nuoyier/ns2-dns-accel), based on commit `23abadf786ea9613a2387810e5a807ef05a6cb3d`, retaining the original [MIT License](LICENSE). This project does not use the upstream DNS installation script.
