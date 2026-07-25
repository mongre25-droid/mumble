#!/usr/bin/env python3
"""Linux platform-specific tests: autostart (.desktop file), update
(bash swap script), os.startfile() → subprocess.run(['xdg-open', path])
replacements, and The Big Shift sync verification.

Run: python test_linux_platform.py
"""
import errno
import io
import os
import shlex
import shutil
import sys
import tempfile
import types
import zipfile

# Add port directory to path so imports resolve
_port_dir = os.path.dirname(os.path.abspath(__file__))
if _port_dir not in sys.path:
    sys.path.insert(0, _port_dir)

_fails = []


def check(label, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}")
    if not cond:
        _fails.append(label)


# ============================================ autostart (.desktop file)
print("\n== autostart: XDG .desktop file API ==")

import autostart

# Verify the .desktop path is XDG-native
check("desktop path is in ~/.config/autostart",
      ".config" in autostart.DESKTOP_PATH and "autostart" in autostart.DESKTOP_PATH)
check("desktop name cannot collide with Mumble VoIP",
      autostart.DESKTOP_NAME == "mumble-voice-to-text.desktop")

# Verify the .desktop content
entry = autostart._build_desktop_entry()
check(".desktop has [Desktop Entry] header", "[Desktop Entry]" in entry)
check(".desktop has Type=Application", "Type=Application" in entry)
check(".desktop has Name=Mumble", "Name=Mumble" in entry)
check(".desktop has Exec line", "Exec=" in entry)
check(".desktop has Terminal=false", "Terminal=false" in entry)
check(".desktop has X-GNOME-Autostart-enabled=true",
      "X-GNOME-Autostart-enabled=true" in entry)
check(".desktop has Categories", "Categories=" in entry)

# XDG_CONFIG_HOME must be honoured only when absolute. The uninstaller uses the
# same XDG root, so this is also an install/uninstall lifecycle invariant.
_old_xdg_config = os.environ.get("XDG_CONFIG_HOME")
try:
    with tempfile.TemporaryDirectory() as _xdg_cfg:
        os.environ["XDG_CONFIG_HOME"] = _xdg_cfg
        check("absolute XDG_CONFIG_HOME is honoured",
              autostart._xdg_config_home() == _xdg_cfg)
        os.environ["XDG_CONFIG_HOME"] = "relative/config"
        check("relative XDG_CONFIG_HOME is ignored",
              autostart._xdg_config_home() != "relative/config")
finally:
    if _old_xdg_config is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = _old_xdg_config

check("desktop Exec doubles literal percent field codes",
      "%%f" in autostart._desktop_quote("/tmp/Mumble %f/launcher"))
try:
    autostart._desktop_quote("/tmp/Mumble\nInjected=true")
    check("desktop Exec rejects control-character injection", False)
except ValueError:
    check("desktop Exec rejects control-character injection", True)

# Write and re-read a .desktop file
tmp = os.path.join(tempfile.gettempdir(),
                   f"_mumble_test_autostart_linux_{os.getpid()}.desktop")
try:
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(entry)
    check("autostart._desktop_valid returns True for valid file",
          autostart._desktop_valid(tmp))
    # Test with invalid file
    invalid_tmp = tmp + ".invalid"
    with open(invalid_tmp, "w", encoding="utf-8") as f:
        f.write("not a desktop file")
    check("autostart._desktop_valid returns False for invalid file",
          not autostart._desktop_valid(invalid_tmp))
    with open(invalid_tmp, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nExec=/bin/true\nHidden=true\n")
    check("autostart._desktop_valid respects Hidden=true",
          not autostart._desktop_valid(invalid_tmp))
    with open(invalid_tmp, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\n")
    check("autostart._desktop_valid requires Exec",
          not autostart._desktop_valid(invalid_tmp))
    with open(invalid_tmp, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nName=Mumble VoIP\nExec=mumble\n")
    check("legacy ownership check preserves Mumble VoIP autostart",
          not autostart._legacy_entry_owned(invalid_tmp))
    with open(invalid_tmp, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nName=Mumble\n"
                "Comment=Private, on-device voice-to-text\nExec=/tmp/mumble\n")
    check("legacy ownership check recognises voice-to-text entry",
          autostart._legacy_entry_owned(invalid_tmp))
    try:
        os.remove(invalid_tmp)
    except OSError:
        pass
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

# Unique-ID lifecycle: an unrelated Mumble VoIP entry must survive both enable
# and disable, while an owned pre-migration entry is recognised and migrated.
with tempfile.TemporaryDirectory() as _autostart_root:
    _saved_autostart = (
        autostart.AUTOSTART_DIR, autostart.DESKTOP_PATH,
        autostart.LEGACY_DESKTOP_PATH)
    autostart.AUTOSTART_DIR = _autostart_root
    autostart.DESKTOP_PATH = os.path.join(
        _autostart_root, "mumble-voice-to-text.desktop")
    autostart.LEGACY_DESKTOP_PATH = os.path.join(
        _autostart_root, "mumble.desktop")
    try:
        with open(autostart.LEGACY_DESKTOP_PATH, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\n"
                    "Name=Mumble VoIP\nExec=mumble\n")
        check("enabling preserves unrelated Mumble VoIP autostart",
              autostart.enable()
              and os.path.isfile(autostart.DESKTOP_PATH)
              and os.path.isfile(autostart.LEGACY_DESKTOP_PATH))
        check("disabling preserves unrelated Mumble VoIP autostart",
              autostart.disable()
              and not os.path.exists(autostart.DESKTOP_PATH)
              and os.path.isfile(autostart.LEGACY_DESKTOP_PATH))
        with open(autostart.LEGACY_DESKTOP_PATH, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Mumble\n"
                    "Comment=Private, on-device voice-to-text\n"
                    "Exec=/tmp/mumble\n")
        check("owned legacy autostart is detected",
              autostart.is_enabled())
        check("enabling migrates owned generic autostart ID",
              autostart.enable()
              and os.path.isfile(autostart.DESKTOP_PATH)
              and not os.path.exists(autostart.LEGACY_DESKTOP_PATH))
    finally:
        (autostart.AUTOSTART_DIR, autostart.DESKTOP_PATH,
         autostart.LEGACY_DESKTOP_PATH) = _saved_autostart

# Verify API surface
check("is_enabled is callable", callable(autostart.is_enabled))
check("enable is callable", callable(autostart.enable))
check("disable is callable", callable(autostart.disable))
check("set_enabled is callable", callable(autostart.set_enabled))
check("install_start_menu no-op", autostart.install_start_menu() is True)
check("remove_start_menu no-op", autostart.remove_start_menu() is True)
check("install_desktop_shortcut no-op", autostart.install_desktop_shortcut() is True)
check("remove_desktop_shortcut no-op", autostart.remove_desktop_shortcut() is True)

# Verify NO Windows-isms
_as_src = open(autostart.__file__, encoding="utf-8").read()
check("no winreg import", "winreg" not in _as_src)
check("no WScript.Shell", "WScript.Shell" not in _as_src)
check("no .lnk creation", "CreateShortcut" not in _as_src)
check("uses XDG .desktop spec", ".desktop" in _as_src)
check("no PowerShell references", "PowerShell" not in _as_src)


# ============================================ branding / XDG data paths
print("\n== branding: XDG data + cross-filesystem migration ==")

import branding

check("Linux PNG icon path is exported",
      branding.ICON_PNG.endswith("mumble.png")
      and os.path.isfile(branding.ICON_PNG))

_old_platform = branding.sys.platform
_old_xdg_data = os.environ.get("XDG_DATA_HOME")
_old_test_data = os.environ.pop("MUMBLE_TEST_DATA_DIR", None)
try:
    branding.sys.platform = "linux"
    with tempfile.TemporaryDirectory() as _xdg_data:
        os.environ["XDG_DATA_HOME"] = _xdg_data
        check("absolute XDG_DATA_HOME is honoured",
              branding._resolve_data_dir()
              == os.path.join(_xdg_data, branding.APP_NAME))
        os.environ["XDG_DATA_HOME"] = "relative/data"
        check("relative XDG_DATA_HOME is ignored",
              not branding._resolve_data_dir().startswith("relative" + os.sep))
finally:
    branding.sys.platform = _old_platform
    if _old_xdg_data is None:
        os.environ.pop("XDG_DATA_HOME", None)
    else:
        os.environ["XDG_DATA_HOME"] = _old_xdg_data
    if _old_test_data is not None:
        os.environ["MUMBLE_TEST_DATA_DIR"] = _old_test_data

# Simulate EXDEV to ensure legacy ~/Mumble data is copied rather than abandoned
# when XDG_DATA_HOME is on another mount.
with tempfile.TemporaryDirectory() as _migration_root:
    _legacy_home = os.path.join(_migration_root, "home")
    _legacy = os.path.join(_legacy_home, branding.APP_NAME)
    _native = os.path.join(_migration_root, "other-volume", branding.APP_NAME)
    os.makedirs(_legacy)
    with open(os.path.join(_legacy, "history.json"), "w", encoding="utf-8") as f:
        f.write("[]")
    _orig_data_dir = branding.DATA_DIR
    _orig_expanduser = branding.os.path.expanduser
    _orig_replace = branding.os.replace
    _orig_platform = branding.sys.platform
    try:
        branding.sys.platform = "linux"
        branding.DATA_DIR = _native
        branding.os.path.expanduser = (
            lambda value: _legacy_home if value == "~" else _orig_expanduser(value))

        def _raise_exdev(_src, _dst):
            raise OSError(errno.EXDEV, "cross-device link")

        branding.os.replace = _raise_exdev
        branding._adopt_legacy_data_dir()
        check("legacy data migrates across filesystems",
              os.path.isfile(os.path.join(_native, "history.json"))
              and not os.path.exists(_legacy))
    finally:
        branding.sys.platform = _orig_platform
        branding.DATA_DIR = _orig_data_dir
        branding.os.path.expanduser = _orig_expanduser
        branding.os.replace = _orig_replace

# A replaced stale marker belongs to the successor and must not be removed by
# the original owner's context-manager cleanup.
if os.name != "nt":
    import storage_lock
    with tempfile.TemporaryDirectory() as _lock_root:
        _data_path = os.path.join(_lock_root, "history.json")
        _lock_path = _data_path + ".lock"
        with storage_lock.exclusive_file_lock(_data_path) as _acquired:
            check("storage lock acquires", _acquired)
            with storage_lock.exclusive_file_lock(
                    _data_path, timeout=0.05) as _second:
                check("POSIX storage lock excludes a concurrent writer",
                      not _second)
            os.remove(_lock_path)
            with open(_lock_path, "w", encoding="ascii") as _successor:
                _successor.write("successor")
        with open(_lock_path, encoding="ascii") as _successor:
            _successor_text = _successor.read()
        check("storage lock cleanup preserves a successor's marker",
              os.path.isfile(_lock_path) and _successor_text == "successor")


# ============================================ update (bash swap script)
print("\n== update: bash swap script (Linux) ==")

import update

# Verify the _write_swap_script function exists
check("_write_swap_script is callable", callable(update._write_swap_script))

# Test the swap script generation on a temp dir
_test_parent = tempfile.mkdtemp(prefix="mumble_test_update_linux_")
_test_new = os.path.join(_test_parent, "Mumble $new '1.0.0'")
_test_cur = os.path.join(_test_parent, "Mumble `current` %f")
os.makedirs(_test_new, exist_ok=True)
os.makedirs(_test_cur, exist_ok=True)

try:
    # Force Linux branch
    orig_plat = sys.platform
    try:
        # Use a non-win, non-darwin platform to hit the else (Linux) branch
        sys.platform = "linux"
        update._write_swap_script(_test_parent, _test_new, _test_cur)
        check("pending swap marker validates exact install and payload",
              update.pending_swap_script(_test_cur)
              == os.path.join(_test_parent, "apply_update.sh"))
    finally:
        sys.platform = orig_plat

    script_path = os.path.join(_test_parent, "apply_update.sh")
    check("writes apply_update.sh", os.path.exists(script_path))

    if os.path.exists(script_path):
        with open(script_path) as f:
            content = f.read()
        check(".sh uses portable env bash shebang",
              content.startswith("#!/usr/bin/env bash"))
        check("waits on the old PID without taskkill",
              "kill -0" in content and "taskkill" not in content)
        check("uses mv (not rename)", "mv " in content and "rename" not in content)
        check("no .bat commands", "taskkill" not in content and "timeout" not in content)
        check("has rollback logic", "backup" in content)
        check("relaunches mumble_linux.py", "mumble_linux.py" in content)
        check("does not hardcode /usr/bin/python3",
              "/usr/bin/python3" not in content)
        check("does not signal a potentially recycled PID",
              f"kill {os.getpid()}" not in content)
        check("shell-quotes metacharacters in install paths",
              shlex.quote(os.path.abspath(_test_cur)) in content
              and shlex.quote(os.path.abspath(_test_new)) in content)
        _pip_line = next(
            (line for line in content.splitlines() if "pip install" in line), "")
        check("dependency failure triggers rollback",
              "pip install" in content and "rollback_update" in content
              and "|| true" not in _pip_line)
        check("is executable", os.access(script_path, os.X_OK)
              or oct(os.stat(script_path).st_mode)[-3:] == "755")
        bash = shutil.which("bash")
        if bash:
            result = __import__("subprocess").run(
                [bash, "-n", script_path], capture_output=True, text=True)
            check("generated updater passes bash -n", result.returncode == 0)
finally:
    shutil.rmtree(_test_parent, ignore_errors=True)

# A full release may contain both Windows and Linux runtimes. Linux must select
# mumble_linux.py rather than whichever mumble.py os.walk encounters first.
with tempfile.TemporaryDirectory(prefix="mumble_mixed_release_") as _mixed:
    _win_root = os.path.join(_mixed, "Internal", "app")
    _lin_root = os.path.join(_win_root, "Ports", "Linux", "app")
    os.makedirs(_lin_root)
    for _root, _entry in ((_win_root, "mumble.py"),
                          (_lin_root, "mumble_linux.py")):
        open(os.path.join(_root, _entry), "w").close()
        open(os.path.join(_root, "branding.py"), "w").close()
    _old_platform = update.sys.platform
    try:
        update.sys.platform = "linux"
        check("updater selects Linux runtime from mixed release",
              update._find_app_root(_mixed) == _lin_root)
    finally:
        update.sys.platform = _old_platform

with tempfile.TemporaryDirectory(prefix="mumble_bad_handoff_") as _handoff:
    _handoff_current = os.path.join(_handoff, "app")
    os.makedirs(_handoff_current)
    _handoff_script = os.path.join(_handoff, "apply_update.sh")
    with open(_handoff_script, "w", encoding="utf-8") as _file:
        _file.write("#!/usr/bin/env bash\n")
    with open(update._linux_pending_path(_handoff), "w",
              encoding="utf-8") as _file:
        _file.write("[]")
    _old_platform = update.sys.platform
    try:
        update.sys.platform = "linux"
        check("non-object pending update marker fails closed",
              update.pending_swap_script(_handoff_current) == ""
              and not os.path.exists(_handoff_script))
    finally:
        update.sys.platform = _old_platform

# Execute the generated swap end-to-end on Linux with a fake venv interpreter:
# current -> backup, payload -> current, venv carried over, marker self-cleaned.
if os.name != "nt" and shutil.which("bash"):
    with tempfile.TemporaryDirectory(prefix="mumble_update_e2e_") as _swap_root:
        _swap_current = os.path.join(_swap_root, "current app")
        _swap_new = os.path.join(_swap_root, "new $payload")
        _swap_python = os.path.join(
            _swap_current, ".venv", "bin", "python")
        os.makedirs(os.path.dirname(_swap_python))
        os.makedirs(_swap_new)
        with open(os.path.join(_swap_current, "old.txt"), "w") as _f:
            _f.write("old")
        with open(os.path.join(_swap_new, "new.txt"), "w") as _f:
            _f.write("new")
        open(os.path.join(_swap_new, "requirements.txt"), "w").close()
        open(os.path.join(_swap_new, "mumble_linux.py"), "w").close()
        _launched = os.path.join(_swap_root, "launched")
        with open(_swap_python, "w", newline="\n") as _f:
            _f.write("#!/usr/bin/env sh\n")
            _f.write("if [ \"${1:-}\" = '-m' ]; then exit 0; fi\n")
            _f.write(f"touch {shlex.quote(_launched)}\n")
        os.chmod(_swap_python, 0o755)
        _script = update._write_linux_swap_script(
            _swap_root, _swap_new, _swap_current, 2147483647)
        _result = __import__("subprocess").run(
            [shutil.which("bash"), _script], cwd=_swap_root,
            capture_output=True, text=True, timeout=20)
        check("generated updater executes successfully", _result.returncode == 0)
        check("generated updater swaps payload and preserves backup",
              os.path.isfile(os.path.join(_swap_current, "new.txt"))
              and os.path.isfile(os.path.join(
                  _swap_root, "Mumble-backup", "old.txt")))
        check("generated updater carries venv and relaunches",
              os.path.isfile(os.path.join(
                  _swap_current, ".venv", "bin", "python"))
              and os.path.isfile(_launched))
        check("generated updater consumes its hand-off",
              not os.path.exists(_script)
              and not os.path.exists(update._linux_pending_path(_swap_root)))

# Verify the update API
check("check_for_update is callable", callable(update.check_for_update))
check("download_and_install is callable", callable(update.download_and_install))
check("rollback is callable", callable(update.rollback))
check("start_auto_check is callable", callable(update.start_auto_check))

# Network streams and zip metadata are both bounded; test the helpers with tiny
# ceilings so the regression test is fast and deterministic.
try:
    update._copy_bounded(io.BytesIO(b"1234"), io.BytesIO(), 3)
    check("updater bounds actual downloaded bytes", False)
except RuntimeError:
    check("updater bounds actual downloaded bytes", True)

with io.BytesIO() as _zip_buffer:
    with zipfile.ZipFile(_zip_buffer, "w") as _zip_writer:
        _zip_writer.writestr("one.txt", b"1")
        _zip_writer.writestr("two.txt", b"2")
    _zip_buffer.seek(0)
    with zipfile.ZipFile(_zip_buffer, "r") as _zip_reader:
        try:
            update._validate_zip_budget(_zip_reader, max_members=1)
            check("updater bounds archive member count", False)
        except RuntimeError:
            check("updater bounds archive member count", True)
        try:
            update._validate_zip_budget(_zip_reader, max_total_bytes=1)
            check("updater bounds expanded archive bytes", False)
        except RuntimeError:
            check("updater bounds expanded archive bytes", True)

# Verify NO Windows-only paths in _write_swap_script
check("update.py has sys.platform detection",
      "sys.platform" in open(update.__file__, encoding="utf-8").read())


# ============================================ non-root Linux input hooks
print("\n== bindings: non-root evdev hooks without mandatory uinput ==")

# Import under a Linux platform value so keyboard/mouse select their evdev
# backends. Merely importing the modules does not access a real device.
_real_platform = sys.platform
import platform as _input_test_platform
_real_platform_system = _input_test_platform.system
try:
    sys.platform = "linux"
    _input_test_platform.system = lambda: "Linux"
    import bindings as linux_bindings
finally:
    sys.platform = _real_platform
    _input_test_platform.system = _real_platform_system

from keyboard import _nixcommon as _kb_common
from keyboard import _nixkeyboard

_old_paths = linux_bindings._linux_event_paths
_old_readable = linux_bindings._linux_event_is_readable
_old_kb_backend = linux_bindings.keyboard._os_keyboard
_old_mouse_backend = (linux_bindings._mouse._os_mouse
                      if linux_bindings.HAVE_MOUSE else None)
_kb_proc = _kb_common._mumble_unfiltered_from_proc
_kb_by_id = _kb_common._mumble_unfiltered_from_by_id


class _DummyEvdev:
    def __init__(self, path):
        self.path = path


class _InitProbe:
    def __init__(self):
        self.calls = 0

    def init(self):
        self.calls += 1


try:
    linux_bindings._linux_event_paths = lambda kind=None: [
        f"/readable-{kind or 'input'}", f"/blocked-{kind or 'input'}"]
    linux_bindings._linux_event_is_readable = lambda path: "blocked" not in path
    _nixkeyboard.ensure_root()
    check("keyboard module-local root gate accepts readable evdev", True)

    _kb_common._mumble_unfiltered_from_proc = lambda _kind: iter((
        _DummyEvdev("/readable-kbd"), _DummyEvdev("/blocked-kbd")))
    _kb_common._mumble_unfiltered_from_by_id = lambda *_a, **_k: iter(())
    _kb_devices = list(_kb_common.list_devices_from_proc("kbd"))
    check("keyboard backend filters inaccessible event devices",
          [device.path for device in _kb_devices] == ["/readable-kbd"])

    _nixkeyboard.to_name.clear()
    _nixkeyboard.from_name.clear()
    _nixkeyboard.build_tables()
    check("static evdev map supports default Ctrl+Windows and F9",
          bool(_nixkeyboard.from_name["ctrl"])
          and bool(_nixkeyboard.from_name["windows"])
          and bool(_nixkeyboard.from_name["f9"]))
    check("static evdev map canonicalises punctuation",
          (52, ()) in _nixkeyboard.from_name["."]
          and (53, ()) in _nixkeyboard.from_name["/"]
          and (12, ()) in _nixkeyboard.from_name["-"]
          and (40, ()) in _nixkeyboard.from_name["'"]
          and (26, ()) in _nixkeyboard.from_name["["]
          and (27, ()) in _nixkeyboard.from_name["]"])
    try:
        for _punctuation in ("-", "'", "[", "]", ".", "/", "=", ";", ","):
            linux_bindings.keyboard.parse_hotkey(_punctuation)
        _punctuation_parses = True
    except Exception:
        _punctuation_parses = False
    check("configurable punctuation hotkeys parse on Linux",
          _punctuation_parses)
    _malformed_proc = (
        'N: Name="Power Button"\nH: Handlers=kbd\n\n'
        'N: Name="Keyboard"\nH: Handlers=sysrq kbd event7 leds\n')
    check("evdev parser skips handler blocks without event nodes",
          linux_bindings._linux_paths_from_proc(_malformed_proc, "kbd")
          == [os.path.join("/dev/input", "event7")])

    _kb_probe = _InitProbe()
    linux_bindings.keyboard._os_keyboard = _kb_probe
    sys.platform = "linux"
    linux_bindings._ensure_keyboard_backend_ready()
    check("keyboard backend initialization is synchronous", _kb_probe.calls == 1)

    if linux_bindings.HAVE_MOUSE:
        from mouse import _nixcommon as _mouse_common
        from mouse import _nixmouse
        _mouse_proc = _mouse_common._mumble_unfiltered_from_proc
        _mouse_by_id = _mouse_common._mumble_unfiltered_from_by_id
        _mouse_aggregate_class = _mouse_common.AggregatedEventDevice
        _mouse_make_uinput = _mouse_common.make_uinput
        try:
            _nixmouse.ensure_root()
            check("mouse module-local root gate accepts readable evdev", True)
            _mouse_common._mumble_unfiltered_from_proc = lambda _kind: iter((
                _DummyEvdev("/readable-mouse"),
                _DummyEvdev("/blocked-mouse")))
            _mouse_common._mumble_unfiltered_from_by_id = lambda *_a, **_k: iter(())
            _mouse_devices = list(_mouse_common.list_devices_from_proc("mouse"))
            check("mouse backend filters inaccessible event devices",
                  [device.path for device in _mouse_devices]
                  == ["/readable-mouse"])

            _uinput_calls = {"count": 0}

            def _forbidden_uinput():
                _uinput_calls["count"] += 1
                raise AssertionError("mouse listener must not create uinput")

            class _AggregateProbe:
                def __init__(self, devices, output=None):
                    self.devices = devices
                    self.output = output

            _mouse_common.make_uinput = _forbidden_uinput
            _mouse_common.AggregatedEventDevice = _AggregateProbe
            _aggregate = _nixmouse.aggregate_devices("mouse")
            check("mouse listener has no /dev/uinput dependency",
                  _uinput_calls["count"] == 0
                  and isinstance(_aggregate.output,
                                 linux_bindings._ReadOnlyEvdevOutput))

            _mouse_probe = _InitProbe()
            linux_bindings._mouse._os_mouse = _mouse_probe
            linux_bindings._ensure_mouse_backend_ready()
            check("mouse backend initialization is synchronous",
                  _mouse_probe.calls == 1)
        finally:
            _mouse_common._mumble_unfiltered_from_proc = _mouse_proc
            _mouse_common._mumble_unfiltered_from_by_id = _mouse_by_id
            _mouse_common.AggregatedEventDevice = _mouse_aggregate_class
            _mouse_common.make_uinput = _mouse_make_uinput
    else:
        check("mouse package is available for Linux global hooks", False)
finally:
    sys.platform = _real_platform
    linux_bindings._linux_event_paths = _old_paths
    linux_bindings._linux_event_is_readable = _old_readable
    linux_bindings.keyboard._os_keyboard = _old_kb_backend
    if linux_bindings.HAVE_MOUSE:
        linux_bindings._mouse._os_mouse = _old_mouse_backend
    _kb_common._mumble_unfiltered_from_proc = _kb_proc
    _kb_common._mumble_unfiltered_from_by_id = _kb_by_id


# ============================================ local platform-package shim
print("\n== platform package: stdlib compatibility shim ==")

import platform as platform_shim

check("local platform package keeps accelerator API",
      callable(getattr(platform_shim, "select_transcriber", None)))
check("local platform package re-exports stdlib API",
      callable(getattr(platform_shim, "system", None))
      and isinstance(platform_shim.system(), str))


# ============================================ os.startfile() replacement
print("\n== os.startfile() → subprocess.run(['xdg-open', path]) ==")

# Verify no os.startfile in mumble_linux.py
_mm_path = os.path.join(_port_dir, "mumble_linux.py")
with open(_mm_path, encoding="utf-8") as f:
    _ml_src = f.read()
check("mumble_linux.py has no os.startfile()",
      "os.startfile(" not in _ml_src)

# Verify webui_shell.py has no os.startfile and uses xdg-open
_ws_path = os.path.join(_port_dir, "webui_shell.py")
with open(_ws_path, encoding="utf-8") as f:
    _ws_src = f.read()
check("webui_shell.py has no os.startfile()",
      "os.startfile(" not in _ws_src)
check("webui_shell.py uses xdg-open",
      "xdg-open" in _ws_src)

# Linux has no classic Tk fallback; the release builder forbids that Windows UI.
_aw_path = os.path.join(_port_dir, "app_window.py")
check("Windows/Tk app_window.py is absent from Linux",
      not os.path.exists(_aw_path))


# ============================================ GTK web-window integration
print("\n== webui_shell: Linux foreground/topmost/palette integration ==")

import webui_shell


class _FakeNativeWindow:
    def __init__(self):
        self.calls = []

    def set_accept_focus(self, value):
        self.calls.append(("accept_focus", value))

    def set_focus_on_map(self, value):
        self.calls.append(("focus_on_map", value))

    def present(self):
        self.calls.append(("present",))

    def unmaximize(self):
        self.calls.append(("unmaximize",))

    def deiconify(self):
        self.calls.append(("deiconify",))

    def show_all(self):
        self.calls.append(("show_all",))

    def set_icon_from_file(self, path):
        self.calls.append(("icon", path))


class _ImmediateGlib:
    @staticmethod
    def idle_add(callback):
        callback()
        return 1


class _FakeWebviewWindow:
    def __init__(self, title):
        self.title = title
        self.native = _FakeNativeWindow()
        self.gui = types.SimpleNamespace(glib=_ImmediateGlib())
        self.on_top = False
        self.calls = []
        self.dialog_result = ("/tmp/selected.txt",)

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")

    def create_file_dialog(self, **kwargs):
        self.calls.append(("file_dialog", kwargs))
        return self.dialog_result


_fake_title = "Mumble Linux Test"
_fake_window = _FakeWebviewWindow(_fake_title)
_old_webview_module = sys.modules.get("webview")
_old_shell_platform = webui_shell.sys.platform
_old_gdk_backend = os.environ.get("GDK_BACKEND")
_old_forced_gdk = os.environ.get("MUMBLE_FORCED_GDK_BACKEND")
try:
    sys.modules["webview"] = types.SimpleNamespace(windows=[_fake_window])
    webui_shell.sys.platform = "linux"
    os.environ["GDK_BACKEND"] = "x11"
    os.environ["MUMBLE_FORCED_GDK_BACKEND"] = "1"
    _external_env = webui_shell._external_app_env()
    check("external apps do not inherit Mumble-forced GDK backend",
          "GDK_BACKEND" not in _external_env
          and "MUMBLE_FORCED_GDK_BACKEND" not in _external_env)
    webui_shell._bring_to_front(_fake_title)
    check("Linux bring-to-front uses pywebview show+restore",
          _fake_window.calls == ["show", "restore"])
    check("Linux pin uses pywebview on_top",
          webui_shell._set_topmost(_fake_title, True)
          and _fake_window.on_top is True)
    check("Linux Deck palette disables native GTK focus",
          webui_shell._set_noactivate(_fake_title, True)
          and ("accept_focus", False) in _fake_window.native.calls
          and ("focus_on_map", False) in _fake_window.native.calls)
    check("Linux Deck unpalette restores focus",
          webui_shell._set_noactivate(_fake_title, False)
          and ("accept_focus", True) in _fake_window.native.calls
          and ("present",) in _fake_window.native.calls)
    webui_shell._show_palette_front(_fake_title)
    check("Linux pinned restore avoids focus-stealing present",
          ("deiconify",) in _fake_window.native.calls
          and ("show_all",) in _fake_window.native.calls)
    webui_shell._apply_mumble_icon(_fake_title)
    check("Linux web window receives PNG icon",
          ("icon", branding.ICON_PNG) in _fake_window.native.calls)
    _picker_api = types.SimpleNamespace(_window=_fake_window)
    check("file imports use pywebview native picker without Tk",
          webui_shell.Api._pick_file(
              _picker_api, ("Text (*.txt)",)) == "/tmp/selected.txt"
          and "import tkinter" not in _ws_src)
finally:
    webui_shell.sys.platform = _old_shell_platform
    webui_shell._NOACTIVATE_STATE.pop(_fake_title, None)
    if _old_gdk_backend is None:
        os.environ.pop("GDK_BACKEND", None)
    else:
        os.environ["GDK_BACKEND"] = _old_gdk_backend
    if _old_forced_gdk is None:
        os.environ.pop("MUMBLE_FORCED_GDK_BACKEND", None)
    else:
        os.environ["MUMBLE_FORCED_GDK_BACKEND"] = _old_forced_gdk
    if _old_webview_module is None:
        sys.modules.pop("webview", None)
    else:
        sys.modules["webview"] = _old_webview_module


# ============================================ The Big Shift sync verification
print("\n== The Big Shift: mode system sync to Linux ==")

# Re-read mumble_linux.py source for checks
_ml_src = open(_mm_path, encoding="utf-8").read()

# Verify critical imports exist
has_local_engine = any("local_engine" in line
                       for line in _ml_src.splitlines()
                       if line.startswith("import "))
check("mumble_linux imports local_engine", has_local_engine)

has_foreign_boost = any("foreign_boost" in line
                        for line in _ml_src.splitlines()
                        if line.startswith("import "))
check("mumble_linux imports foreign_boost", has_foreign_boost)

# Verify _processing guard
check("mumble_linux has _processing attribute",
      "_processing" in _ml_src)

# Verify prompt_mode_enabled
check("mumble_linux has prompt_mode_enabled",
      "prompt_mode_enabled" in _ml_src)

# Verify active_mode / set_active_mode
check("mumble_linux has active_mode",
      "active_mode" in _ml_src)

# Verify _register_mode_key is a no-op
_rmk_start = _ml_src.index("def _register_mode_key")
_rmk_chunk = _ml_src[_rmk_start:_rmk_start + 800]
check("_register_mode_key is a no-op (does not bind keys)",
      "register_hold" not in _rmk_chunk and "THE BIG SHIFT" in _rmk_chunk)

# Verify _watch_mode_key is a no-op
_wmk_start = _ml_src.index("def _watch_mode_key")
_wmk_chunk = _ml_src[_wmk_start:_wmk_start + 400]
check("_watch_mode_key is a no-op (no polling)",
      "is_pressed" not in _wmk_chunk)

# Verify the immutable processing decision replaced the legacy advisory route.
check("processing_route.snapshot() exists in mumble_linux",
      "processing_route.snapshot(" in _ml_src)

# Verify foreign_boost.boost() is called
check("foreign_boost.boost() exists in mumble_linux",
      "foreign_boost.boost(" in _ml_src)


print("\n" + ("ALL GREEN" if not _fails else f"{len(_fails)} FAILED: {_fails}"))
sys.exit(1 if _fails else 0)
