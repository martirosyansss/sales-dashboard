"""Кэш, записанный из-под root, должен читаться сервисом: новый файл получает владельца папки."""
import os
import stat

import numpy as np
import pytest

from route_optimizer import roads as rd

root_only = pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() != 0, reason='chown только из-под root')
SERVICE = (12345, 12346)


def owner(path):
    st = path.stat()
    return st.st_uid, st.st_gid


@root_only
def test_root_written_npz_belongs_to_folder_owner(tmp_path):
    os.chown(tmp_path, *SERVICE)
    path = tmp_path / 'armenia.dist.npz'
    rd._save_npz(str(path), key=np.array('k'))
    assert owner(path) == SERVICE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert str(np.load(path, allow_pickle=False)['key']) == 'k'


@root_only
def test_root_rewrite_returns_root_owned_cache_to_service(tmp_path):
    os.chown(tmp_path, *SERVICE)
    path = tmp_path / 'armenia.dist.npz'
    path.write_bytes(b'old')
    path.chmod(0o600)
    assert owner(path) == (0, 0)
    rd._save_npz(str(path), key=np.array('k'))
    assert owner(path) == SERVICE


def test_service_user_write_does_not_chown(tmp_path, monkeypatch):
    monkeypatch.setattr(rd.os, 'geteuid', lambda: 999, raising=False)
    monkeypatch.setattr(rd.os, 'fchown', lambda *a: pytest.fail('chown не нужен'), raising=False)
    path = tmp_path / 'armenia.dist.npz'
    rd._save_npz(str(path), key=np.array('k'))
    assert path.exists()


def test_platform_without_geteuid_does_not_chown(tmp_path, monkeypatch):
    monkeypatch.delattr(rd.os, 'geteuid', raising=False)
    monkeypatch.setattr(rd.os, 'fchown', lambda *a: pytest.fail('chown не нужен'), raising=False)
    path = tmp_path / 'armenia.dist.npz'
    rd._save_npz(str(path), key=np.array('k'))
    assert path.exists()


def test_relative_path_takes_owner_of_current_folder(tmp_path, monkeypatch):
    seen = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rd.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(rd.os, 'fchown', lambda fd, uid, gid: seen.append((uid, gid)), raising=False)
    rd._save_npz('armenia.dist.npz', key=np.array('k'))
    folder = os.stat('.')
    assert seen == [(folder.st_uid, folder.st_gid)]


def test_refused_chown_leaves_no_temp_file(tmp_path, monkeypatch):
    def deny(*args):
        raise PermissionError('нельзя')
    monkeypatch.setattr(rd.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(rd.os, 'fchown', deny, raising=False)
    with pytest.raises(OSError):
        rd._save_npz(str(tmp_path / 'armenia.dist.npz'), key=np.array('k'))
    assert list(tmp_path.iterdir()) == []
