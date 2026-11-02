#!/usr/bin/env bash
#
# demo_vm.sh: a macOS virtual machine in UTM to record the primitives demo
# (issue #524) in. SWE.app ignores $HOME and reads the logged-in account's
# real ~/.swe, so a take needs a Mac account of its own; a VM is one that
# is thrown away after every take.
#
#   scripts/demo_vm.sh create      install macOS in a new VM, with a `demo` account
#   scripts/demo_vm.sh provision   install swe, the harness CLIs and SWE.app in it
#   scripts/demo_vm.sh save        shut it down and snapshot it as the clean base
#   scripts/demo_vm.sh take        start a throwaway run of the base for one take
#   scripts/demo_vm.sh reset       stop the VM and put the clean base back
#   scripts/demo_vm.sh ssh [CMD]   run CMD, or a shell, in the running VM
#   scripts/demo_vm.sh shot FILE   bring SWE.app to the front and screenshot the VM
#   scripts/demo_vm.sh all         create, provision and save, in that order
#
# Each step is safe to run again: one that already happened says so and
# moves on. `take` starts the VM without saving changes (`utmctl start
# --disposable`), so stopping it is the whole reset; `reset` also restores
# the snapshot `save` made, for when the base was started some other way.
#
# Needs: macOS 27 or later on Apple silicon (the guest account is set up on
# first boot by Virtualization.framework, which only 27 offers), UTM 5.0.6
# or later with utmctl on PATH, and Xcode, since SWE.app is built here and
# copied in: SwiftUI's macros ship with Xcode alone, and the guest has no
# Apple ID to download it with. The first `create` asks macOS to let your
# terminal control UTM; allow it.
#
# Two settings only a click can change, both once: Screen Recording for
# Terminal in the guest (`provision` opens the page and says so), and the
# display size, which UTM's scripting cannot set: with the VM stopped, Edit
# it in UTM, Display, turn off Dynamic Resolution and pick 1920 x 1080.
#
# The guest is isolated on purpose: no shared folders, no clipboard sharing,
# no Apple ID, and no credentials or tokens. Nothing is copied from your
# HOME: the guest gets a key pair and a password made for it, kept in
# SWE_DEMO_VM_STATE, and an app built from a fresh worktree of origin/main.
#
# Overrides:
#   SWE_DEMO_VM        the VM's name (default swe-demo-base)
#   SWE_DEMO_VM_IPSW   the macOS restore image (default: the newest this Mac
#                      supports, downloaded once into SWE_DEMO_VM_CACHE)
#   SWE_DEMO_VM_CACHE  restore images and the app build (default
#                      ~/Library/Caches/swe-demo-vm)
#   SWE_DEMO_VM_STATE  the guest's key pair, password and known_hosts
#                      (default ~/Library/Application Support/swe-demo-vm)

set -euo pipefail

VM="${SWE_DEMO_VM:-swe-demo-base}"
CACHE="${SWE_DEMO_VM_CACHE:-$HOME/Library/Caches/swe-demo-vm}"
STATE="${SWE_DEMO_VM_STATE:-$HOME/Library/Application Support/swe-demo-vm}"
GUEST_USER="demo"
SNAPSHOT="clean-base"
# 80 GB disk, 8 GB of memory, 4 cores: room for Homebrew, Node and the
# harness CLIs, and a recording that does not stutter.
DISK_MIB=81920
MEMORY_MIB=8192
CPUS=4

say() { printf '%s\n' "$*" >&2; }
die() { printf 'demo_vm: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,/^$/s/^# \{0,1\}//p' "$0" >&2
}

repo="$(cd "$(dirname "$0")/.." && pwd -P)"
key="$STATE/id_ed25519"
known_hosts="$STATE/known_hosts"
password_file="$STATE/demo-password"

need_host() {
    [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] \
        || die "needs a Mac with Apple silicon."
    major="$(sw_vers -productVersion | cut -d. -f1)"
    [ "$major" -ge 27 ] || die "needs macOS 27 or later on this Mac (this is $(sw_vers -productVersion))."
    command -v utmctl >/dev/null 2>&1 \
        || die "utmctl is not on PATH; install UTM 5.0.6 or later (Homebrew cask \`utm\`)."
}

# UTM answers AppleScript only while it runs; utmctl starts it the same way.
utm() {
    open -g -a UTM
    osascript "$@"
}

vm_exists() { utmctl status "$VM" >/dev/null 2>&1; }
vm_status() { utmctl status "$VM" 2>/dev/null || echo missing; }

# The VM's notes carry how far setup got ("installed", "provisioned"), so a
# second run skips what the first one finished. UTM changes a VM's
# configuration only while it is stopped, so they are written then.
vm_notes() {
    utm -e "tell application \"UTM\" to get notes of (get configuration of virtual machine named \"$VM\")" 2>/dev/null || true
}
set_vm_notes() {
    utm - "$VM" "$1" <<'EOF' >/dev/null
on run argv
  tell application "UTM"
    set vm to virtual machine named (item 1 of argv)
    set cfg to configuration of vm
    set notes of cfg to (item 2 of argv)
    update configuration of vm with cfg
  end tell
end run
EOF
}

# --- The restore image ---

# Virtualization.framework knows the newest restore image this Mac can run.
# Asking it needs the virtualization entitlement, so the helper is compiled
# and signed ad hoc with it.
latest_ipsw_url() {
    work="$(mktemp -d)"
    cat > "$work/latest.swift" <<'EOF'
import Virtualization
let done = DispatchSemaphore(value: 0)
VZMacOSRestoreImage.fetchLatestSupported { result in
    switch result {
    case .success(let image): print(image.url.absoluteString)
    case .failure(let error): FileHandle.standardError.write("\(error)\n".data(using: .utf8)!)
    }
    done.signal()
}
done.wait()
EOF
    cat > "$work/vz.entitlements" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.virtualization</key><true/></dict></plist>
EOF
    swiftc -O "$work/latest.swift" -o "$work/latest" >/dev/null \
        && codesign -s - --entitlements "$work/vz.entitlements" -f "$work/latest" 2>/dev/null \
        && "$work/latest"
    status=$?
    rm -rf "$work"
    return "$status"
}

ipsw_path() {
    if [ -n "${SWE_DEMO_VM_IPSW:-}" ]; then
        [ -f "$SWE_DEMO_VM_IPSW" ] || die "SWE_DEMO_VM_IPSW=$SWE_DEMO_VM_IPSW is not a file."
        printf '%s\n' "$SWE_DEMO_VM_IPSW"
        return
    fi
    url="$(latest_ipsw_url)" || die "couldn't ask Virtualization.framework for a restore image."
    [ -n "$url" ] || die "Virtualization.framework returned no restore image."
    file="$CACHE/$(basename "$url")"
    mkdir -p "$CACHE"
    if [ ! -f "$file" ]; then
        say "Downloading $(basename "$url") (about 25 GB) ..."
        # -C - resumes a download stopped halfway; the .part name keeps a
        # broken one from passing for a whole image.
        curl -fL --progress-bar -C - -o "$file.part" "$url" || die "download of $url failed; run again to resume."
        mv "$file.part" "$file"
    fi
    printf '%s\n' "$file"
}

# --- Talking to the guest ---

# Apple Virtualization VMs have no QEMU guest agent, so `utmctl exec` and
# `utmctl file` cannot reach a macOS guest; everything goes over SSH.
# `utmctl ip-address` reads the host's ARP table, which can lag a fresh
# boot, so the DHCP lease for the VM's MAC address is the fallback.
guest_ip() {
    ip="$(utmctl ip-address "$VM" 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n 1 || true)"
    if [ -z "$ip" ] && [ -r /var/db/dhcpd_leases ]; then
        mac="$(utm -e "tell application \"UTM\" to get address of item 1 of network interfaces of (get configuration of virtual machine named \"$VM\")" 2>/dev/null || true)"
        # The leases file drops leading zeros from each byte (0a -> a).
        mac="$(printf '%s' "$mac" | tr 'A-F' 'a-f' | sed -E 's/(^|:)0([0-9a-f])/\1\2/g')"
        [ -n "$mac" ] && ip="$(awk -v mac="1,$mac" '
            /ip_address=/ { sub(/.*ip_address=/, ""); addr = $0 }
            /hw_address=/ { sub(/.*hw_address=/, ""); if ($0 == mac) found = addr }
            END { if (found) print found }' /var/db/dhcpd_leases)"
    fi
    printf '%s' "$ip"
}

ssh_opts=(-i "$key" -o IdentitiesOnly=yes -o BatchMode=yes
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$known_hosts"
    -o ConnectTimeout=5 -o LogLevel=ERROR)

guest() {
    ip="$(guest_ip)"
    [ -n "$ip" ] || die "$VM has no IP address yet; is it running? ($(vm_status))"
    ssh "${ssh_opts[@]}" "$GUEST_USER@$ip" "$@"
}

wait_for_ssh() {
    ip="$(guest_ip)"
    if [ -n "$ip" ] && nc -z -G 2 "$ip" 22 >/dev/null 2>&1; then
        printf '%s' "$ip"
        return 0
    fi
    say "Waiting for $VM to answer on SSH ..."
    for _ in $(seq 1 120); do
        ip="$(guest_ip)"
        if [ -n "$ip" ] && nc -z -G 2 "$ip" 22 >/dev/null 2>&1; then
            printf '%s' "$ip"
            return 0
        fi
        sleep 5
    done
    die "$VM did not answer on SSH within 10 minutes."
}

wait_for_status() {
    for _ in $(seq 1 120); do
        [ "$(vm_status)" = "$1" ] && return 0
        sleep 2
    done
    die "$VM did not reach $1 (it is $(vm_status))."
}

# --- Steps ---

cmd_create() {
    need_host
    mkdir -p "$STATE"
    chmod 700 "$STATE"
    [ -f "$key" ] || ssh-keygen -q -t ed25519 -N '' -C "$VM" -f "$key"
    if [ ! -f "$password_file" ]; then
        (umask 077; LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 > "$password_file")
    fi

    if vm_exists; then
        say "$VM already exists."
    else
        ipsw="$(ipsw_path)"
        say "Creating $VM from $(basename "$ipsw") ..."
        utm - "$VM" "$ipsw" "$MEMORY_MIB" "$CPUS" "$DISK_MIB" <<'EOF' >/dev/null
on run argv
  tell application "UTM"
    make new virtual machine with properties {backend:apple, configuration:{name:(item 1 of argv), os:macOS, macRecoveryIpsw:(POSIX file (item 2 of argv)), memory:((item 3 of argv) as integer), cpu cores:((item 4 of argv) as integer), drives:{{guest size:((item 5 of argv) as integer)}}}}
  end tell
end run
EOF
    fi

    case "$(vm_notes)" in
        *installed* | *provisioned*) say "macOS is already installed in $VM." ;;
        *)
            say "Installing macOS in $VM (15 to 30 minutes) ..."
            utm -e "tell application \"UTM\" to install virtual machine named \"$VM\"" >/dev/null
            while :; do
                progress="$(utm -e "tell application \"UTM\" to query install virtual machine named \"$VM\"" 2>&1)" \
                    || die "the install failed: $progress"
                case "$progress" in *"installing:false"*) break ;; esac
                printf '\r  %s' "$(printf '%s' "$progress" | sed -n 's/.*progress:\([0-9.]*\).*/\1/p')" >&2
                sleep 15
            done
            say ""
            set_vm_notes "installed"
            # The first start after the install creates the account, logs it
            # in automatically and turns on Remote Login; it applies only to
            # that one start and is never saved.
            say "First boot: creating the $GUEST_USER account ..."
            utmctl start "$VM" --provision-username="$GUEST_USER" --provision-full-name="Demo" \
                --provision-password-stdin --provision-auto-login --provision-ssh < "$password_file"
            ;;
    esac

    [ "$(vm_status)" = started ] || utmctl start "$VM"
    ip="$(wait_for_ssh)"
    authorize_key "$ip"
    say "$VM is up at $ip; next: scripts/demo_vm.sh provision"
}

# Key login, and sudo without a password, so provisioning runs unattended.
# The password exists only to get this far: it is typed once, by ssh's
# askpass, and never stored in the guest.
authorize_key() {
    if ssh "${ssh_opts[@]}" "$GUEST_USER@$1" true 2>/dev/null; then
        return
    fi
    say "Installing the demo key in $VM ..."
    askpass="$(mktemp)"
    printf '#!/bin/sh\ncat %q\n' "$password_file" > "$askpass"
    chmod 700 "$askpass"
    pub="$(cat "$key.pub")"
    # The password goes to sudo on stdin, in front of the sudoers line.
    SSH_ASKPASS="$askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=none \
        ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$known_hosts" \
        -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive \
        -o LogLevel=ERROR "$GUEST_USER@$1" "
        mkdir -p ~/.ssh && chmod 700 ~/.ssh
        grep -qxF '$pub' ~/.ssh/authorized_keys 2>/dev/null || echo '$pub' >> ~/.ssh/authorized_keys
        chmod 600 ~/.ssh/authorized_keys
        sudo -S -p '' sh -c 'echo \"$GUEST_USER ALL=(ALL) NOPASSWD: ALL\" > /etc/sudoers.d/$GUEST_USER && chmod 440 /etc/sudoers.d/$GUEST_USER'
    " < "$password_file"
    rm -f "$askpass"
    ssh "${ssh_opts[@]}" "$GUEST_USER@$1" true || die "key login to $VM failed after installing the key."
}

# SWE.app from a fresh worktree of origin/main, signed ad hoc. The source
# path stamped in it is the guest's clone, which Settings > Updates reads.
build_app() {
    command -v xcodebuild >/dev/null 2>&1 && xcodebuild -version >/dev/null 2>&1 \
        || die "building SWE.app needs Xcode selected (xcode-select -s /Applications/Xcode.app)."
    src="$CACHE/app-src"
    git -C "$repo" fetch --quiet origin
    sha="$(git -C "$repo" rev-parse origin/main)"
    git -C "$repo" worktree prune
    if [ -e "$src/.git" ]; then
        git -C "$src" checkout --quiet --detach --force "$sha"
        git -C "$src" clean -fdq
    else
        rm -rf "$src"
        git -C "$repo" worktree add --quiet --detach "$src" "$sha"
    fi
    say "Building SWE.app from origin/main ($(git -C "$src" rev-parse --short HEAD)) ..."
    rm -rf "$CACHE/app-out"
    SWE_BUILD_DIR="$CACHE/app-out" SWE_SOURCE_PATH="/Users/$GUEST_USER/swe" SWE_SIGN_IDENTITY=- \
        "$src/app/mac/script/build-app.sh" release >"$CACHE/build-app.log" 2>&1 \
        || die "the SWE.app build failed; see $CACHE/build-app.log"
}

cmd_provision() {
    need_host
    [ "$(vm_status)" = started ] || { utmctl start "$VM"; }
    ip="$(wait_for_ssh)"
    authorize_key "$ip"

    say "Installing the command line tools, Homebrew, swe and the harness CLIs ..."
    # Written to a file and run with stdin closed: fed to `bash -s`, the
    # script would be stdin for brew and npm too, and they eat the rest of it.
    guest 'cat > /tmp/swe-demo-provision.sh' <<'EOF'
set -euo pipefail
# Command Line Tools: git, and the compilers Homebrew wants.
if ! xcode-select -p >/dev/null 2>&1; then
    flag=/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
    sudo touch "$flag"
    label="$(softwareupdate -l 2>/dev/null | sed -n 's/^\* Label: \(Command Line Tools.*\)$/\1/p' | sort -V | tail -n 1)"
    [ -n "$label" ] || { echo "no Command Line Tools in softwareupdate -l" >&2; exit 1; }
    sudo softwareupdate -i "$label"
    sudo rm -f "$flag"
fi
if [ ! -x /opt/homebrew/bin/brew ]; then
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# .zshenv, not .zprofile: `ssh host cmd` runs a non-login zsh.
if ! grep -q 'brew shellenv' ~/.zshenv 2>/dev/null; then
    {
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"'
        echo 'export PATH="$HOME/.local/bin:$PATH"'
    } >> ~/.zshenv
fi
eval "$(/opt/homebrew/bin/brew shellenv)"
export PATH="$HOME/.local/bin:$PATH"
# Every take starts at a fresh login: no Terminal windows from the last one.
# Terminal keeps its saved windows in a container SSH cannot clear, so it is
# told to ignore them instead.
defaults write com.apple.Terminal NSQuitAlwaysKeepsWindows -bool false
defaults write com.apple.Terminal ApplePersistenceIgnoreState -bool true
defaults write com.apple.loginwindow TALLogoutSavesState -bool false
brew install --quiet python@3.12 node pipx
pipx install --force "git+https://github.com/c-wenlong/swe.git"
npm install -g --silent @anthropic-ai/claude-code @openai/codex
if [ -d ~/swe/.git ]; then
    git -C ~/swe fetch --quiet origin && git -C ~/swe checkout --quiet main && git -C ~/swe reset --quiet --hard origin/main
else
    git clone --quiet https://github.com/c-wenlong/swe.git ~/swe
fi
EOF
    guest 'bash /tmp/swe-demo-provision.sh </dev/null && rm -f /tmp/swe-demo-provision.sh'

    build_app
    say "Copying SWE.app into $VM ..."
    guest 'mkdir -p ~/Applications && rm -rf ~/Applications/SWE.app'
    tar -C "$CACHE/app-out" -cf - SWE.app | guest 'tar -C ~/Applications -xf -'

    # One launch, with its first-run sheet already answered: the demo runs
    # `swe setup` on camera instead. The app writes ~/.swe/data and caches
    # on launch; those go too, so the base has no ~/.swe at all.
    say "Launching SWE.app once ..."
    guest 'defaults write com.cwenlong.swe firstRunSetupDone -bool true
        open ~/Applications/SWE.app; sleep 20
        osascript -e "tell application id \"com.cwenlong.swe\" to quit" || pkill -x SWE || true
        sleep 3; rm -rf ~/.swe'

    # The menu bar clock is on camera: show this Mac's time, not Cupertino's.
    tz="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
    if [ -n "$tz" ]; then
        guest "sudo systemsetup -settimezone '$tz' >/dev/null 2>&1 \
            || sudo ln -sf '/var/db/timezone/zoneinfo/$tz' /etc/localtime"
    fi

    check_stage

    if ! guest_capture /tmp/swe-demo-check.png; then
        # Screen Recording can only be granted by a click, and only to an
        # app that asked: the failed capture above put Terminal in the list.
        # Stopping here keeps `all` from saving a base without it.
        guest 'open "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_ScreenCapture"'
        die "screenshots need one click in $VM's window: in the System Settings it
opened (Screen & System Audio Recording), turn on Terminal and enter the demo
password ($password_file). Then check with scripts/demo_vm.sh shot, allow
the prompt it shows in the VM, and run scripts/demo_vm.sh save."
    fi
    say "Provisioned. Next: scripts/demo_vm.sh save"
}

# A screenshot of the guest's screen, at PATH in the guest. An SSH session
# has no display, so Terminal takes it in the logged-in session; Terminal
# needs Screen Recording, which `provision` asks for.
guest_capture() {
    guest "cat > /tmp/swe-demo-capture.command" <<EOF
#!/bin/sh
open -a "\$HOME/Applications/SWE.app"
sleep 2
screencapture -x '$1' && touch '$1.done'
# Close this window once its shell has exited, so it never asks to.
nohup sh -c 'sleep 1; osascript -e "tell application \\"Terminal\\" to close (every window whose name contains \\"swe-demo-capture\\")"' >/dev/null 2>&1 &
EOF
    guest "chmod +x /tmp/swe-demo-capture.command; rm -f '$1' '$1.done'
        open -a Terminal /tmp/swe-demo-capture.command
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do test -f '$1.done' && exit 0; sleep 1; done
        exit 1"
}

cmd_shot() {
    need_host
    [ -n "${1:-}" ] || die "shot needs a file to write: scripts/demo_vm.sh shot FILE.png"
    guest_capture /tmp/swe-demo-shot.png \
        || die "the screenshot failed; is Terminal allowed under Screen Recording in $VM?"
    guest 'cat /tmp/swe-demo-shot.png' > "$1"
    say "Saved $1"
}

# docs/demo.md's own setup line, run once and undone, to prove the stage
# builds in this account.
check_stage() {
    say "Checking scripts/demo_home.sh in $VM ..."
    guest 'set -e; cd ~/swe
        eval "$(scripts/demo_home.sh --root=/tmp/swe-demo --home="$HOME")"
        test -d "$DEMO_FORK" || test -d "${DEMO_FORK#file://}"
        rm -rf /tmp/swe-demo
        # demo_home.sh made these only because they were missing.
        rmdir ~/.claude ~/.codex 2>/dev/null || true
        git config --file ~/.gitconfig user.email | grep -qx demo@example.com && rm -f ~/.gitconfig
        test ! -e ~/.swe'
}

cmd_save() {
    need_host
    if [ "$(vm_status)" = started ]; then
        guest 'test -d ~/Applications/SWE.app && command -v swe claude codex >/dev/null' \
            || die "$VM is missing SWE.app or a CLI; run provision first."
        # Launching SWE.app writes ~/.swe/data and caches, and the CLIs write
        # their state (~/.codex's databases) the first time anything runs
        # them. Those go, so a take starts as a fresh account; a hub in ~/.swe
        # means a take ran in the base, which is not a clean base at all.
        guest 'test ! -e ~/.swe/AGENTS.md && test ! -e ~/.swe/.git' \
            || die "$VM has a hub in ~/.swe; set up a fresh base (or remove it by hand) first."
        # Terminal's saved windows and zsh's saved sessions would reopen at
        # every take's login.
        guest 'osascript -e "tell application id \"com.cwenlong.swe\" to quit" >/dev/null 2>&1 || true
            pkill -x Terminal || true
            sleep 2; rm -rf ~/.swe ~/.codex ~/.claude ~/.claude.json ~/.zsh_sessions \
                ~/Library/Saved\ Application\ State/com.apple.Terminal.savedState'
        say "Shutting $VM down ..."
        guest 'sudo shutdown -h now' >/dev/null 2>&1 || true
        wait_for_status stopped
        set_vm_notes "provisioned"
    fi
    case "$(vm_notes)" in
        *provisioned*) ;;
        *) die "start $VM and run provision first; it has not been saved as provisioned." ;;
    esac
    if utmctl snapshot list "$VM" 2>/dev/null | grep -q "$SNAPSHOT"; then
        utmctl snapshot overwrite "$VM" --snapshot="$SNAPSHOT"
    else
        utmctl snapshot create "$VM" --name="$SNAPSHOT"
    fi
    say "Saved $VM as the clean base (snapshot $SNAPSHOT)."
}

cmd_take() {
    need_host
    [ "$(vm_status)" = stopped ] || die "$VM is $(vm_status); stop it first (scripts/demo_vm.sh reset)."
    utmctl start "$VM" --disposable
    ip="$(wait_for_ssh)"
    say ""
    say "$VM is running without saving changes, at $ip."
    say "In its window, open Terminal, then follow docs/demo.md from \"Before you record\":"
    say "  cd ~/swe && eval \"\$(scripts/demo_home.sh --root=/tmp/swe-demo --home=\"\$HOME\")\""
    say "Stop it when the take is done: scripts/demo_vm.sh reset"
}

cmd_reset() {
    need_host
    if [ "$(vm_status)" != stopped ]; then
        utmctl stop "$VM" --force >/dev/null 2>&1 || utmctl stop "$VM" --kill
        wait_for_status stopped
    fi
    if utmctl snapshot list "$VM" 2>/dev/null | grep -q "$SNAPSHOT"; then
        utmctl snapshot restore "$VM" --snapshot="$SNAPSHOT"
    fi
    say "$VM is stopped and back at the clean base."
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
    create) cmd_create ;;
    provision) cmd_provision ;;
    save) cmd_save ;;
    take) cmd_take ;;
    reset) cmd_reset ;;
    ssh) need_host; wait_for_ssh >/dev/null; guest "$@" ;;
    shot) cmd_shot "$@" ;;
    all) cmd_create; cmd_provision; cmd_save ;;
    -h | --help | help) usage ;;
    "") usage; exit 1 ;;
    *) die "unknown command: $cmd (see --help)" ;;
esac
