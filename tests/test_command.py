from dww_launcher.command import LaunchOptions, build_args, build_command, resolve_executable


def test_basic_args():
    opts = LaunchOptions(port="/ports/dsda-doom", iwad="/iwads/doom2.wad")
    assert build_command(opts) == ["/ports/dsda-doom", "-iwad", "/iwads/doom2.wad"]


def test_full_args():
    opts = LaunchOptions(
        port="dsda-doom",
        iwad="doom2.wad",
        pwads=["sunlust.wad", "fix.deh", "music.wad", "patch.BEX"],
        map="MAP07",
        skill=4,
        complevel=2,
        fast=True,
        nomonsters=True,
        record="/demos/run",
        extra_args='-nomusic -config "my config.cfg"',
    )
    assert build_args(opts) == [
        "-iwad", "doom2.wad",
        "-file", "sunlust.wad", "music.wad",
        "-deh", "fix.deh", "patch.BEX",
        "-complevel", "2",
        "-warp", "7",
        "-skill", "4",
        "-fast",
        "-nomonsters",
        "-record", "/demos/run",
        "-nomusic", "-config", "my config.cfg",
    ]


def test_episode_warp():
    opts = LaunchOptions(iwad="doom.wad", map="E3M2")
    assert build_args(opts)[-3:] == ["-warp", "3", "2"]


def test_playdemo_ignores_gameplay_options():
    opts = LaunchOptions(iwad="doom2.wad", map="MAP01", skill=4, fast=True,
                         record="x", playdemo="demo.lmp")
    assert build_args(opts) == ["-iwad", "doom2.wad", "-playdemo", "demo.lmp"]


def test_resolve_app_bundle(tmp_path):
    bundle = tmp_path / "GZDoom.app"
    exe = bundle / "Contents" / "MacOS" / "GZDoom"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert resolve_executable(bundle) == exe
    plain = tmp_path / "dsda-doom"
    assert resolve_executable(plain) == plain
