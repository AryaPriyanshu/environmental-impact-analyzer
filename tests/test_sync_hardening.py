from __future__ import annotations

import os

import pytest

import src.sync_official_data as sync_data


class _Response:
    def __init__(self, chunks: list[bytes], *, content_length: str | None = None):
        self.url = "https://data.example.org/file.csv"
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size == sync_data.DOWNLOAD_CHUNK_BYTES
        yield from self._chunks


def test_remote_payload_reader_enforces_declared_and_streamed_limits():
    with pytest.raises(RuntimeError, match="download limit"):
        sync_data._bounded_response_bytes(
            _Response([], content_length="11"),
            max_bytes=10,
            label="test CSV",
            allowed_host_suffixes=("example.org",),
        )
    with pytest.raises(RuntimeError, match="download limit"):
        sync_data._bounded_response_bytes(
            _Response([b"123456", b"78901"]),
            max_bytes=10,
            label="test CSV",
            allowed_host_suffixes=("example.org",),
        )


def test_download_url_rejects_credentials_private_hosts_and_host_substitution():
    with pytest.raises(RuntimeError):
        sync_data._validate_download_url("https://user:secret@example.org/file")
    with pytest.raises(RuntimeError):
        sync_data._validate_download_url("https://127.0.0.1/file")
    with pytest.raises(RuntimeError, match="not allowed"):
        sync_data._validate_download_url(
            "https://evil.example/file",
            allowed_host_suffixes=("data.gouv.fr",),
        )
    assert sync_data._public_source_url("javascript:alert(1)", "https://example.org") == "https://example.org"
    assert sync_data._safe_id_component(" id/with\ncontrols ") == "id-with-controls"


def test_atomic_publish_rolls_back_all_replaced_files(monkeypatch, tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "metadata.json"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    real_replace = os.replace
    failed = False

    def fail_second_once(source, destination):
        nonlocal failed
        if not failed and destination == second:
            failed = True
            raise OSError("simulated publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(sync_data.os, "replace", fail_second_once)
    with pytest.raises(OSError, match="simulated"):
        sync_data._atomic_publish({first: b"new first", second: b"new second"})
    assert first.read_bytes() == b"old first"
    assert second.read_bytes() == b"old second"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_repairability_csv_uses_bounded_downloader(monkeypatch):
    resource_url = "https://static.data.gouv.fr/repair.csv"
    monkeypatch.setattr(
        sync_data,
        "_fetch_json",
        lambda *args, **kwargs: {
            "resources": [{"format": "csv", "title": "Dernière version", "url": resource_url}]
        },
    )

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(sync_data, "_session", _Session)
    calls = []

    def download(session, url, **kwargs):
        calls.append((url, kwargs))
        return (
            b"categorie_produit,note_ir,id_unique,nom_modele,id_modele,nom_metteur_sur_le_marche,date_calcul\n"
            b"smartphone,8.2,one,Phone,P1,Maker,2026-01-01\n"
        )

    monkeypatch.setattr(sync_data, "_download_bytes", download)
    records, source = sync_data.fetch_repairability()
    assert len(records) == 1
    assert source["resource"] == resource_url
    assert calls[0][1]["max_bytes"] == sync_data.MAX_CSV_DOWNLOAD_BYTES
