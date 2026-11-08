import os

from dww_launcher.desktop import clean_environment

SEP = os.pathsep


def test_not_frozen_leaves_environment_alone():
    env = {"LD_LIBRARY_PATH": "/x", "QT_PLUGIN_PATH": "/y"}
    assert clean_environment(env, bundle="") == env


def test_removes_bundle_paths(tmp_path):
    bundle = str(tmp_path / "_internal")
    env = {
        "LD_LIBRARY_PATH": f"{bundle}{SEP}/usr/local/lib",
        "QT_PLUGIN_PATH": f"{bundle}/PySide6/Qt/plugins",
        "QML2_IMPORT_PATH": f"{bundle}/PySide6/Qt/qml",
        "PATH": f"{bundle}{SEP}/usr/bin{SEP}/bin",
        "HOME": "/home/me",
    }
    out = clean_environment(env, bundle=bundle)
    assert out == {"LD_LIBRARY_PATH": "/usr/local/lib", "PATH": f"/usr/bin{SEP}/bin", "HOME": "/home/me"}
    assert env["QT_PLUGIN_PATH"]  # input not modified


def test_restores_saved_originals(tmp_path):
    bundle = str(tmp_path / "b")
    env = {"LD_LIBRARY_PATH": bundle, "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    assert clean_environment(env, bundle=bundle) == {"LD_LIBRARY_PATH": "/opt/lib"}


def test_similar_prefix_is_kept(tmp_path):
    bundle = str(tmp_path / "app")
    env = {"LD_LIBRARY_PATH": str(tmp_path / "app-other")}
    assert clean_environment(env, bundle=bundle) == env
