#!/usr/bin/env bash
# Installer for mousebat: udev rule, systemd user unit, optional autostart.
#
#   ./install.sh                  install and enable autostart
#   ./install.sh --no-autostart   install and run now, but do not start at login
#   ./install.sh --uninstall      remove everything this script installed
#   ./install.sh --help

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="mousebat.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RULE_NAME="42-mousebat-hidraw.rules"
RULE_DIR="/etc/udev/rules.d"

AUTOSTART=1
ACTION="install"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
step()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --no-autostart) AUTOSTART=0 ;;
        --uninstall)    ACTION="uninstall" ;;
        -h|--help)      usage ;;
        *) red "Unknown option: $arg"; echo "Try --help."; exit 2 ;;
    esac
done

require_not_root() {
    if [[ ${EUID} -eq 0 ]]; then
        red "Do not run this as root."
        info "The unit and its autostart belong to your user session; the script"
        info "asks for sudo only for the udev rule."
        exit 1
    fi
}

# --- checks ---------------------------------------------------------------

check_python_deps() {
    step "Checking dependencies"
    if python3 -c 'import PyQt6.QtWidgets' 2>/dev/null; then
        info "PyQt6: present"
        return
    fi
    info "PyQt6: missing"
    if command -v apt-get >/dev/null 2>&1; then
        echo "  Installing python3-pyqt6 (needs sudo)..."
        sudo apt-get install -y python3-pyqt6
    else
        red "PyQt6 is required. Install your distribution's python3-pyqt6 package."
        exit 1
    fi
}

check_systemd_user() {
    if ! systemctl --user show-environment >/dev/null 2>&1; then
        red "No systemd user session available."
        info "The applet itself will still run via: python3 -m mousebat"
        exit 1
    fi
}

# --- install steps --------------------------------------------------------

install_rule() {
    step "Installing udev rule"
    local target="${RULE_DIR}/${RULE_NAME}"
    if sudo cmp -s "${PROJECT_DIR}/packaging/${RULE_NAME}" "${target}" 2>/dev/null; then
        info "already up to date: ${target}"
        return
    fi
    sudo install -m 0644 "${PROJECT_DIR}/packaging/${RULE_NAME}" "${target}"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --action=add --subsystem-match=hidraw
    info "installed: ${target}"
}

verify_access() {
    step "Checking device access"
    local output
    if ! output="$(cd "${PROJECT_DIR}" && python3 tools/spike_probe.py 2>&1)"; then
        red "Could not read any device."
        printf '%s\n' "${output}" | sed 's/^/    /'
        info "Log out and back in if the ACL has not been applied yet."
        return 1
    fi
    printf '%s\n' "${output}" | tail -3 | sed 's/^/    /'
}

install_unit() {
    step "Installing systemd user unit"
    mkdir -p "${UNIT_DIR}"
    # The unit template carries a placeholder so the project can live anywhere.
    sed "s|@PROJECT_DIR@|${PROJECT_DIR}|g" \
        "${PROJECT_DIR}/packaging/${UNIT_NAME}" > "${UNIT_DIR}/${UNIT_NAME}"
    systemctl --user daemon-reload
    info "installed: ${UNIT_DIR}/${UNIT_NAME}"
    info "project dir: ${PROJECT_DIR}"
}

start_service() {
    step "Starting"
    if [[ ${AUTOSTART} -eq 1 ]]; then
        systemctl --user enable --now "${UNIT_NAME}"
        info "autostart: on"
    else
        systemctl --user disable "${UNIT_NAME}" >/dev/null 2>&1 || true
        systemctl --user restart "${UNIT_NAME}"
        info "autostart: off (running until you log out)"
    fi
    sleep 3
    if systemctl --user is-active --quiet "${UNIT_NAME}"; then
        green "mousebat is running."
    else
        red "The service did not start."
        info "Logs: journalctl --user -u mousebat -n 30"
        exit 1
    fi
}

do_install() {
    require_not_root
    check_systemd_user
    check_python_deps
    install_rule
    install_unit
    verify_access || true
    start_service

    step "Done"
    info "Autostart can be toggled from the tray menu: 'Start at login'."
    info "Or here:  systemctl --user enable|disable ${UNIT_NAME}"
    info "Logs:     journalctl --user -u mousebat -f"
}

# --- uninstall ------------------------------------------------------------

do_uninstall() {
    require_not_root

    step "Stopping the service"
    systemctl --user disable --now "${UNIT_NAME}" >/dev/null 2>&1 || true
    info "stopped, autostart off"

    step "Removing the unit"
    rm -f "${UNIT_DIR}/${UNIT_NAME}"
    systemctl --user daemon-reload
    info "removed: ${UNIT_DIR}/${UNIT_NAME}"

    step "Removing the udev rule"
    if [[ -f "${RULE_DIR}/${RULE_NAME}" ]]; then
        sudo rm -f "${RULE_DIR}/${RULE_NAME}"
        sudo udevadm control --reload-rules
        info "removed: ${RULE_DIR}/${RULE_NAME}"
    else
        info "not present, nothing to do"
    fi

    step "Done"
    info "python3-pyqt6 was left installed; remove it yourself if unwanted."
    info "The project directory itself is untouched: ${PROJECT_DIR}"
}

case "${ACTION}" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
esac
