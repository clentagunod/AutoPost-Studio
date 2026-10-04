"""Access tokens via the platform credential vault; never store them in JSON."""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "AutoPost Studio"


def get_secret(name: str) -> str:
    try:
        return keyring.get_password(SERVICE, name) or ""
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(f"Credential vault is unavailable: {exc}") from exc


def set_secret(name: str, value: str) -> None:
    try:
        keyring.set_password(SERVICE, name, value)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(f"Credential vault is unavailable: {exc}") from exc


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(f"Credential vault is unavailable: {exc}") from exc
