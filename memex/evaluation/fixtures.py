"""Phase 5 fixture histories: small repositories whose evidence changes mid-task.

A *history* combines a **template** (a small codebase whose dependency has a
contract) with a **mechanism** (how a change to that evidence arrives). Each
history carries hidden checks, a gold implementation for the world after the
change, and a stale implementation written from the original contract. A
history is admitted to trials only if its checks separate the two
(`admit`), so a non-discriminating fixture is rejected before any agent runs.

Nothing an agent can read contains the answer: hidden checks, gold patches
and post-change file contents live only here and in the harness state
directory, never in the trial repository.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

FIXTURE_VERSION = "phase5-fixtures.v1"


# --------------------------------------------------------------------------- #
# Templates: one small codebase each, with a dependency contract that changes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Template:
    name: str
    target: str            # the file the agent edits
    target_stub: str
    dep: str               # the dependency module (file name)
    symbol: str            # the dependency function
    renamed: str           # its name after the `removed` mechanism
    helper: str            # helper module for the `helper` mechanism
    dep_old: str
    dep_new: str
    dep_via_helper: str
    helper_old: str
    helper_new: str
    dep_renamed: str
    prompt: str
    assertion: str         # the original contract, as a claim
    new_assertion: str     # the changed contract, as an approved decision
    rule: str              # an approved rule the change does not supersede
    gold_old: str
    gold_new: str
    gold_renamed: str
    checks: str            # hidden checks: functions named check_*


def _t(**kw) -> Template:
    return Template(**{k: (v.strip("\n") + "\n" if isinstance(v, str) and "\n" in v else v)
                       for k, v in kw.items()})


TEMPLATES: dict[str, Template] = {}

TEMPLATES["payments"] = _t(
    name="payments", target="api.py", dep="validate.py", symbol="validate", renamed="validate_payload",
    helper="rules.py",
    target_stub='''
from validate import validate


def send(payload):
    raise NotImplementedError("send is not implemented yet")
''',
    dep_old='''
def validate(payload):
    """Return True when the payload is usable, False when it is not."""
    return bool(payload)
''',
    dep_new='''
def validate(payload):
    """Raise ValueError when the payload is unusable; return True otherwise."""
    if not payload:
        raise ValueError("payload is unusable")
    return True
''',
    dep_via_helper='''
from rules import usable


def validate(payload):
    """Decide whether a payload is usable (see rules.usable)."""
    return usable(payload)
''',
    helper_old='''
def usable(payload):
    """Return True when the payload is usable, False when it is not."""
    return bool(payload)
''',
    helper_new='''
def usable(payload):
    """Raise ValueError when the payload is unusable; return True otherwise."""
    if not payload:
        raise ValueError("payload is unusable")
    return True
''',
    dep_renamed='''
def validate_payload(payload):
    """Return True when the payload is usable, False when it is not."""
    return bool(payload)
''',
    prompt=("Implement send(payload) in api.py. It must return {'ok': True, 'value': payload} for a usable "
            "payload and {'ok': False, 'error': 'invalid'} for an unusable one, and it must never raise. "
            "Decide whether a payload is usable with the check in validate.py."),
    assertion=("validate(payload) in validate.py returns False for an unusable payload and never raises, so "
               "callers can branch on its boolean result without handling exceptions."),
    new_assertion="validate(payload) in validate.py raises ValueError for an unusable payload.",
    rule="send() must never raise; an unusable payload yields {'ok': False, 'error': 'invalid'}.",
    gold_old='''
from validate import validate


def send(payload):
    if validate(payload):
        return {"ok": True, "value": payload}
    return {"ok": False, "error": "invalid"}
''',
    gold_new='''
from validate import validate


def send(payload):
    try:
        validate(payload)
    except ValueError:
        return {"ok": False, "error": "invalid"}
    return {"ok": True, "value": payload}
''',
    gold_renamed='''
from validate import validate_payload


def send(payload):
    if validate_payload(payload):
        return {"ok": True, "value": payload}
    return {"ok": False, "error": "invalid"}
''',
    checks='''
from api import send


def check_usable():
    assert send("hello") == {"ok": True, "value": "hello"}


def check_unusable_empty():
    assert send("") == {"ok": False, "error": "invalid"}


def check_unusable_none():
    assert send(None) == {"ok": False, "error": "invalid"}
''',
)

TEMPLATES["inventory"] = _t(
    name="inventory", target="weights.py", dep="units.py", symbol="to_grams", renamed="grams_from",
    helper="factors.py",
    target_stub='''
from units import to_grams


def total_grams(items):
    raise NotImplementedError("total_grams is not implemented yet")
''',
    dep_old='''
GRAMS = {"g": 1, "kg": 1000, "lb": 453.6}


def to_grams(qty, unit):
    """Return qty converted to grams, or None when the unit is unknown."""
    factor = GRAMS.get(unit)
    return None if factor is None else qty * factor
''',
    dep_new='''
GRAMS = {"g": 1, "kg": 1000, "lb": 453.6}


def to_grams(qty, unit):
    """Return qty converted to grams; raise KeyError when the unit is unknown."""
    if unit not in GRAMS:
        raise KeyError(f"unknown unit: {unit}")
    return qty * GRAMS[unit]
''',
    dep_via_helper='''
from factors import factor_for


def to_grams(qty, unit):
    """Convert qty to grams using factors.factor_for."""
    factor = factor_for(unit)
    return None if factor is None else qty * factor
''',
    helper_old='''
GRAMS = {"g": 1, "kg": 1000, "lb": 453.6}


def factor_for(unit):
    """Return the grams factor for unit, or None when the unit is unknown."""
    return GRAMS.get(unit)
''',
    helper_new='''
GRAMS = {"g": 1, "kg": 1000, "lb": 453.6}


def factor_for(unit):
    """Return the grams factor for unit; raise KeyError when the unit is unknown."""
    if unit not in GRAMS:
        raise KeyError(f"unknown unit: {unit}")
    return GRAMS[unit]
''',
    dep_renamed='''
GRAMS = {"g": 1, "kg": 1000, "lb": 453.6}


def grams_from(qty, unit):
    """Return qty converted to grams, or None when the unit is unknown."""
    factor = GRAMS.get(unit)
    return None if factor is None else qty * factor
''',
    prompt=("Implement total_grams(items) in weights.py. items is a list of (qty, unit) pairs. Return "
            "{'grams': <total grams of the items whose unit is known>, 'skipped': <number of items with an "
            "unknown unit>}. It must never raise for an unknown unit. Convert with the function in units.py."),
    assertion=("to_grams(qty, unit) in units.py returns None for an unknown unit and never raises, so callers "
               "can test the result for None."),
    new_assertion="to_grams(qty, unit) in units.py raises KeyError for an unknown unit.",
    rule="total_grams() must never raise; items with an unknown unit are counted as skipped.",
    gold_old='''
from units import to_grams


def total_grams(items):
    grams, skipped = 0, 0
    for qty, unit in items:
        value = to_grams(qty, unit)
        if value is None:
            skipped += 1
        else:
            grams += value
    return {"grams": grams, "skipped": skipped}
''',
    gold_new='''
from units import to_grams


def total_grams(items):
    grams, skipped = 0, 0
    for qty, unit in items:
        try:
            grams += to_grams(qty, unit)
        except KeyError:
            skipped += 1
    return {"grams": grams, "skipped": skipped}
''',
    gold_renamed='''
from units import grams_from


def total_grams(items):
    grams, skipped = 0, 0
    for qty, unit in items:
        value = grams_from(qty, unit)
        if value is None:
            skipped += 1
        else:
            grams += value
    return {"grams": grams, "skipped": skipped}
''',
    checks='''
from weights import total_grams


def check_known():
    assert total_grams([(2, "kg"), (500, "g")]) == {"grams": 2500, "skipped": 0}


def check_unknown_unit():
    assert total_grams([(1, "kg"), (3, "stone")]) == {"grams": 1000, "skipped": 1}


def check_empty():
    assert total_grams([]) == {"grams": 0, "skipped": 0}
''',
)

TEMPLATES["textkit"] = _t(
    name="textkit", target="stats.py", dep="tokens.py", symbol="tokenize", renamed="words",
    helper="splitter.py",
    target_stub='''
from tokens import tokenize


def summary(text):
    raise NotImplementedError("summary is not implemented yet")
''',
    dep_old='''
def tokenize(text):
    """Return a list of the words in text."""
    return text.split()
''',
    dep_new='''
def tokenize(text):
    """Return an iterator over the words in text (not a list)."""
    return iter(text.split())
''',
    dep_via_helper='''
from splitter import split_words


def tokenize(text):
    """Split text into words (see splitter.split_words)."""
    return split_words(text)
''',
    helper_old='''
def split_words(text):
    """Return a list of the words in text."""
    return text.split()
''',
    helper_new='''
def split_words(text):
    """Return an iterator over the words in text (not a list)."""
    return iter(text.split())
''',
    dep_renamed='''
def words(text):
    """Return a list of the words in text."""
    return text.split()
''',
    prompt=("Implement summary(text) in stats.py. Return {'words': <number of words>, 'first': <the first "
            "word, or None for text with no words>}. Split text into words with the function in tokens.py."),
    assertion=("tokenize(text) in tokens.py returns a list, so callers can take len() of the result and index "
               "into it."),
    new_assertion="tokenize(text) in tokens.py returns an iterator, not a list; it has no len() and no indexing.",
    rule="summary() reports the word count and the first word, or None when there are no words.",
    gold_old='''
from tokens import tokenize


def summary(text):
    words = tokenize(text)
    return {"words": len(words), "first": words[0] if words else None}
''',
    gold_new='''
from tokens import tokenize


def summary(text):
    words = list(tokenize(text))
    return {"words": len(words), "first": words[0] if words else None}
''',
    gold_renamed='''
from tokens import words as split


def summary(text):
    words = split(text)
    return {"words": len(words), "first": words[0] if words else None}
''',
    checks='''
from stats import summary


def check_sentence():
    assert summary("the quick fox") == {"words": 3, "first": "the"}


def check_empty():
    assert summary("   ") == {"words": 0, "first": None}
''',
)

TEMPLATES["scheduler"] = _t(
    name="scheduler", target="shifts.py", dep="windows.py", symbol="parse_window", renamed="window_bounds",
    helper="clock.py",
    target_stub='''
from windows import parse_window


def shift_minutes(spec):
    raise NotImplementedError("shift_minutes is not implemented yet")
''',
    dep_old='''
def parse_window(spec):
    """Return (start, end) for an 'HH:MM-HH:MM' spec, in minutes since midnight."""
    start, end = spec.split("-")
    return _minutes(start), _minutes(end)


def _minutes(text):
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)
''',
    dep_new='''
def parse_window(spec):
    """Return (start, end) for an 'HH:MM-HH:MM' spec, in SECONDS since midnight."""
    start, end = spec.split("-")
    return _seconds(start), _seconds(end)


def _seconds(text):
    hours, minutes = text.split(":")
    return (int(hours) * 60 + int(minutes)) * 60
''',
    dep_via_helper='''
from clock import since_midnight


def parse_window(spec):
    """Return (start, end) for an 'HH:MM-HH:MM' spec (units: see clock.since_midnight)."""
    start, end = spec.split("-")
    return since_midnight(start), since_midnight(end)
''',
    helper_old='''
def since_midnight(text):
    """Return minutes since midnight for 'HH:MM'."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)
''',
    helper_new='''
def since_midnight(text):
    """Return SECONDS since midnight for 'HH:MM'."""
    hours, minutes = text.split(":")
    return (int(hours) * 60 + int(minutes)) * 60
''',
    dep_renamed='''
def window_bounds(spec):
    """Return (start, end) for an 'HH:MM-HH:MM' spec, in minutes since midnight."""
    start, end = spec.split("-")
    return _minutes(start), _minutes(end)


def _minutes(text):
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)
''',
    prompt=("Implement shift_minutes(spec) in shifts.py. spec is an 'HH:MM-HH:MM' window within one day. "
            "Return the length of the window in whole minutes, as an int. Parse the window with the "
            "function in windows.py."),
    assertion="parse_window(spec) in windows.py returns (start, end) in minutes since midnight.",
    new_assertion="parse_window(spec) in windows.py returns (start, end) in seconds since midnight.",
    rule="shift_minutes() returns the window length in whole minutes as an int.",
    gold_old='''
from windows import parse_window


def shift_minutes(spec):
    start, end = parse_window(spec)
    return end - start
''',
    gold_new='''
from windows import parse_window


def shift_minutes(spec):
    start, end = parse_window(spec)
    return (end - start) // 60
''',
    gold_renamed='''
from windows import window_bounds


def shift_minutes(spec):
    start, end = window_bounds(spec)
    return end - start
''',
    checks='''
from shifts import shift_minutes


def check_day_shift():
    assert shift_minutes("09:00-17:00") == 480


def check_short():
    assert shift_minutes("00:00-00:30") == 30
''',
)

TEMPLATES["pricing"] = _t(
    name="pricing", target="checkout.py", dep="rates.py", symbol="tax_rate", renamed="rate_for",
    helper="table.py",
    target_stub='''
from rates import tax_rate


def gross(net, region):
    raise NotImplementedError("gross is not implemented yet")
''',
    dep_old='''
RATES = {"uk": 20, "de": 19, "us": 0}


def tax_rate(region):
    """Return the tax rate for region as a percentage (20 means 20%)."""
    return RATES[region]
''',
    dep_new='''
RATES = {"uk": 0.20, "de": 0.19, "us": 0.0}


def tax_rate(region):
    """Return the tax rate for region as a FRACTION (0.2 means 20%)."""
    return RATES[region]
''',
    dep_via_helper='''
from table import lookup


def tax_rate(region):
    """Return the tax rate for region (units: see table.lookup)."""
    return lookup(region)
''',
    helper_old='''
RATES = {"uk": 20, "de": 19, "us": 0}


def lookup(region):
    """Return the rate for region as a percentage (20 means 20%)."""
    return RATES[region]
''',
    helper_new='''
RATES = {"uk": 0.20, "de": 0.19, "us": 0.0}


def lookup(region):
    """Return the rate for region as a FRACTION (0.2 means 20%)."""
    return RATES[region]
''',
    dep_renamed='''
RATES = {"uk": 20, "de": 19, "us": 0}


def rate_for(region):
    """Return the tax rate for region as a percentage (20 means 20%)."""
    return RATES[region]
''',
    prompt=("Implement gross(net, region) in checkout.py. Return the price including tax for that region, "
            "rounded to 2 decimal places. Get the region's rate with the function in rates.py."),
    assertion="tax_rate(region) in rates.py returns a percentage: 20 means 20%.",
    new_assertion="tax_rate(region) in rates.py returns a fraction: 0.2 means 20%.",
    rule="gross() returns the tax-inclusive price rounded to 2 decimal places.",
    gold_old='''
from rates import tax_rate


def gross(net, region):
    return round(net * (1 + tax_rate(region) / 100), 2)
''',
    gold_new='''
from rates import tax_rate


def gross(net, region):
    return round(net * (1 + tax_rate(region)), 2)
''',
    gold_renamed='''
from rates import rate_for


def gross(net, region):
    return round(net * (1 + rate_for(region) / 100), 2)
''',
    checks='''
from checkout import gross


def check_uk():
    assert gross(100, "uk") == 120.0


def check_de():
    assert gross(50, "de") == 59.5


def check_untaxed():
    assert gross(10, "us") == 10.0
''',
)

TEMPLATES["config"] = _t(
    name="config", target="client.py", dep="settings.py", symbol="get", renamed="lookup",
    helper="store.py",
    target_stub='''
from settings import get


def timeout_seconds(cfg):
    raise NotImplementedError("timeout_seconds is not implemented yet")
''',
    dep_old='''
def get(cfg, key):
    """Return cfg[key], or None when the key is absent."""
    return cfg.get(key)
''',
    dep_new='''
def get(cfg, key):
    """Return cfg[key]; raise KeyError when the key is absent."""
    if key not in cfg:
        raise KeyError(key)
    return cfg[key]
''',
    dep_via_helper='''
from store import fetch


def get(cfg, key):
    """Read a setting (see store.fetch)."""
    return fetch(cfg, key)
''',
    helper_old='''
def fetch(cfg, key):
    """Return cfg[key], or None when the key is absent."""
    return cfg.get(key)
''',
    helper_new='''
def fetch(cfg, key):
    """Return cfg[key]; raise KeyError when the key is absent."""
    if key not in cfg:
        raise KeyError(key)
    return cfg[key]
''',
    dep_renamed='''
def lookup(cfg, key):
    """Return cfg[key], or None when the key is absent."""
    return cfg.get(key)
''',
    prompt=("Implement timeout_seconds(cfg) in client.py. cfg is a dict of settings whose values are "
            "strings. Return the 'timeout' setting as an int, or 30 when it is not set. It must never raise "
            "for a missing setting. Read the setting with the function in settings.py."),
    assertion="get(cfg, key) in settings.py returns None when the key is absent and never raises.",
    new_assertion="get(cfg, key) in settings.py raises KeyError when the key is absent.",
    rule="timeout_seconds() never raises for a missing setting and defaults to 30.",
    gold_old='''
from settings import get


def timeout_seconds(cfg):
    value = get(cfg, "timeout")
    return 30 if value is None else int(value)
''',
    gold_new='''
from settings import get


def timeout_seconds(cfg):
    try:
        return int(get(cfg, "timeout"))
    except KeyError:
        return 30
''',
    gold_renamed='''
from settings import lookup


def timeout_seconds(cfg):
    value = lookup(cfg, "timeout")
    return 30 if value is None else int(value)
''',
    checks='''
from client import timeout_seconds


def check_set():
    assert timeout_seconds({"timeout": "5"}) == 5


def check_missing():
    assert timeout_seconds({"retries": "2"}) == 30
''',
)

TEMPLATES["users"] = _t(
    name="users", target="greet.py", dep="directory.py", symbol="find_name", renamed="name_of",
    helper="records.py",
    target_stub='''
from directory import find_name


def greeting(user_id):
    raise NotImplementedError("greeting is not implemented yet")
''',
    dep_old='''
USERS = {1: "Ada", 2: "Grace"}


def find_name(user_id):
    """Return the user's name, or None when there is no such user."""
    return USERS.get(user_id)
''',
    dep_new='''
USERS = {1: "Ada", 2: "Grace"}


def find_name(user_id):
    """Return the user's name; raise LookupError when there is no such user."""
    if user_id not in USERS:
        raise LookupError(user_id)
    return USERS[user_id]
''',
    dep_via_helper='''
from records import fetch_name


def find_name(user_id):
    """Look up a user's name (see records.fetch_name)."""
    return fetch_name(user_id)
''',
    helper_old='''
USERS = {1: "Ada", 2: "Grace"}


def fetch_name(user_id):
    """Return the user's name, or None when there is no such user."""
    return USERS.get(user_id)
''',
    helper_new='''
USERS = {1: "Ada", 2: "Grace"}


def fetch_name(user_id):
    """Return the user's name; raise LookupError when there is no such user."""
    if user_id not in USERS:
        raise LookupError(user_id)
    return USERS[user_id]
''',
    dep_renamed='''
USERS = {1: "Ada", 2: "Grace"}


def name_of(user_id):
    """Return the user's name, or None when there is no such user."""
    return USERS.get(user_id)
''',
    prompt=("Implement greeting(user_id) in greet.py. Return 'Hello, <name>!' for a known user and "
            "'Hello, guest!' for an unknown one. It must never raise. Look the name up with the function "
            "in directory.py."),
    assertion="find_name(user_id) in directory.py returns None for an unknown user and never raises.",
    new_assertion="find_name(user_id) in directory.py raises LookupError for an unknown user.",
    rule="greeting() never raises; an unknown user is greeted as guest.",
    gold_old='''
from directory import find_name


def greeting(user_id):
    name = find_name(user_id)
    return f"Hello, {name}!" if name is not None else "Hello, guest!"
''',
    gold_new='''
from directory import find_name


def greeting(user_id):
    try:
        return f"Hello, {find_name(user_id)}!"
    except LookupError:
        return "Hello, guest!"
''',
    gold_renamed='''
from directory import name_of


def greeting(user_id):
    name = name_of(user_id)
    return f"Hello, {name}!" if name is not None else "Hello, guest!"
''',
    checks='''
from greet import greeting


def check_known():
    assert greeting(1) == "Hello, Ada!"


def check_unknown():
    assert greeting(99) == "Hello, guest!"
''',
)

#: Development templates. The confirmatory set is drawn only from the others.
DEV_TEMPLATES = ("payments", "inventory", "textkit")
CONFIRMATORY_TEMPLATES = ("scheduler", "pricing", "config", "users")


# --------------------------------------------------------------------------- #
# Mechanisms: how a change to the evidence arrives
# --------------------------------------------------------------------------- #

#: mechanism -> (fixture, label). "affected": the change makes the original
#: contract wrong for this task. "stable": a correct original solution stays
#: correct, so any interruption is a false interruption.
MECHANISMS = {
    "direct": ("F01", "affected"),
    "helper": ("F01", "affected"),
    "removed": ("F02", "affected"),
    "superseded": ("F03", "affected"),
    "governed": ("F06", "affected"),
    "merged": ("F09", "affected"),
    "checkout": ("F13", "affected"),
    "unrelated": ("F04", "stable"),
    "alternative": ("F05", "stable"),
    "worktree": ("F08", "stable"),
    "cosmetic": ("-", "stable"),
    "control": ("-", "stable"),
}


@dataclass(frozen=True)
class History:
    history_id: str
    template: str
    mechanism: str
    split: str             # "development" | "confirmatory"

    @property
    def spec(self) -> Template:
        return TEMPLATES[self.template]

    @property
    def fixture(self) -> str:
        return MECHANISMS[self.mechanism][0]

    @property
    def label(self) -> str:
        return MECHANISMS[self.mechanism][1]

    @property
    def affected(self) -> bool:
        return self.label == "affected"


#: The 12 development histories: three templates, each mechanism once.
DEV_HISTORIES = tuple(History(f"D{i + 1:02d}", template, mechanism, "development") for i, (template, mechanism) in
                      enumerate((("payments", "direct"), ("inventory", "helper"), ("textkit", "removed"),
                                 ("payments", "superseded"), ("inventory", "unrelated"),
                                 ("textkit", "alternative"), ("payments", "governed"), ("inventory", "worktree"),
                                 ("textkit", "merged"), ("payments", "checkout"), ("inventory", "cosmetic"),
                                 ("textkit", "control"))))


def confirmatory_histories(per_template: tuple[str, ...]) -> tuple[History, ...]:
    """Every confirmatory template crossed with the given mechanisms, in a fixed order."""
    return tuple(History(f"C{i + 1:02d}", template, mechanism, "confirmatory")
                 for i, (template, mechanism) in enumerate(
                     (t, m) for t in CONFIRMATORY_TEMPLATES for m in per_template))


# --------------------------------------------------------------------------- #
# Worlds: the repository before and after the change
# --------------------------------------------------------------------------- #

README = "# {name}\n\nSmall {name} module used in examples.\n\n## Contracts\n\n- {assertion}\n"
DECISIONS = "# Approved decisions\n\n- D1 (approved by the maintainer): {text}\n"
DECISIONS_V2 = ("# Approved decisions\n\n- D1 (approved by the maintainer, superseded by D2): {old}\n"
                "- D2 (approved by the maintainer, supersedes D1): {new}\n")
MAIN_NOTE = "# Main branch review note.\n"
UNRELATED = ("CHANGELOG.md", "# Changelog\n\n- 0.1.0: initial version\n",
             "# Changelog\n\n- 0.1.1: documentation tidy-up\n- 0.1.0: initial version\n")


def _visible_test(t: Template) -> str:
    return (f'"""Documents {t.dep}\'s contract."""\nfrom {t.dep[:-3]} import {t.symbol}\n\n\n'
            f"def test_contract_is_documented():\n    assert callable({t.symbol})\n")


def before_files(h: History) -> dict[str, str]:
    """Every file the trial repository starts with. Nothing here reveals the change."""
    t = h.spec
    files = {t.target: t.target_stub, "README.md": README.format(name=t.name, assertion=t.assertion),
             UNRELATED[0]: UNRELATED[1]}
    if h.mechanism == "helper":
        files[t.dep], files[t.helper] = t.dep_via_helper, t.helper_old
    elif h.mechanism == "merged":
        files[t.dep] = MAIN_NOTE + t.dep_old  # main's own commit, which later conflicts with the feature
    else:
        files[t.dep] = t.dep_old
    if h.mechanism == "alternative":
        files[f"tests/test_{t.dep}"] = _visible_test(t)
    if h.mechanism in ("superseded", "governed"):
        text = t.assertion if h.mechanism == "superseded" else t.rule
        files["DECISIONS.md"] = DECISIONS.format(text=text)
    return files


def after_files(h: History) -> dict[str, str]:
    """The trial repository's files once the change has landed (agent edits aside)."""
    t = h.spec
    files = dict(before_files(h))
    m = h.mechanism
    if m in ("direct", "governed", "checkout"):
        files[t.dep] = t.dep_new
    elif m == "merged":
        files[t.dep] = merged_dep(t)
    elif m == "helper":
        files[t.helper] = t.helper_new
    elif m == "removed":
        files[t.dep] = t.dep_renamed
    elif m == "superseded":
        files[t.dep] = t.dep_new
        files["DECISIONS.md"] = DECISIONS_V2.format(old=t.assertion, new=t.new_assertion)
    elif m == "unrelated":
        files[UNRELATED[0]] = UNRELATED[2]
    elif m == "alternative":
        files["README.md"] = files["README.md"].replace("## Contracts", "## Contracts (reviewed)")
    elif m == "cosmetic":
        files[t.dep] = "# Reviewed: no behavior change.\n" + t.dep_old
    # worktree, control: this checkout does not change
    return files


def merged_dep(t: Template) -> str:
    """The resolution of the merge conflict: the feature branch's behavior with main's note."""
    return "# Merged from feature; keeps main's review note.\n" + t.dep_new


def gold(h: History) -> str:
    t = h.spec
    if h.mechanism == "removed":
        return t.gold_renamed
    return t.gold_new if h.affected else t.gold_old


def stale(h: History) -> str:
    """What an agent writes from the original contract."""
    return h.spec.gold_old


# --------------------------------------------------------------------------- #
# Hidden checks
# --------------------------------------------------------------------------- #

RUNNER = r'''
import importlib, json, sys, traceback
sys.path.insert(0, ".")
results = {}
try:
    module = importlib.import_module("hidden_checks")
except BaseException as exc:
    print(json.dumps({"$import": "fail:" + type(exc).__name__ + ":" + str(exc)[:200]}))
    sys.exit(0)
for name in sorted(n for n in dir(module) if n.startswith("check_")):
    try:
        getattr(module, name)()
        results[name] = "pass"
    except BaseException as exc:
        results[name] = "fail:" + type(exc).__name__ + ":" + str(exc)[:200]
print(json.dumps(results))
'''


def run_checks(h: History, files: dict[str, str], target_source: str, *, timeout: float = 60) -> dict[str, str]:
    """Run the hidden checks against `target_source` in a world given by `files`.

    The checks execute in a temporary directory outside any trial repository,
    so an agent never sees them. Every check result is returned by name; an
    import failure is reported as `$import`.
    """
    with tempfile.TemporaryDirectory(prefix="memex-p5-checks-") as scratch:
        root = Path(scratch)
        for relative, content in files.items():
            if relative.endswith(".py") and "/" not in relative:
                (root / relative).write_text(content, encoding="utf-8", newline="\n")
        (root / h.spec.target).write_text(target_source, encoding="utf-8", newline="\n")
        (root / "hidden_checks.py").write_text(h.spec.checks, encoding="utf-8", newline="\n")
        (root / "runner.py").write_text(RUNNER, encoding="utf-8", newline="\n")
        try:
            done = subprocess.run([sys.executable, "-I", "runner.py"], cwd=root, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"$timeout": f"fail:timeout after {timeout}s"}
        try:
            return json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return {"$runner": "fail:" + (done.stderr or done.stdout)[-300:]}


def check_names(h: History) -> list[str]:
    return sorted(line.split("(")[0][4:] for line in h.spec.checks.splitlines() if line.startswith("def check_"))


def failed_checks(h: History, result: dict[str, str]) -> set[str]:
    """Checks that did not pass. A module that cannot even be imported, a runner
    failure or a timeout fails every check."""
    names = set(check_names(h))
    if any(key.startswith("$") for key in result):
        return names
    return {n for n in names if result.get(n) != "pass"}


@dataclass
class Admission:
    history: History
    admitted: bool
    probe: tuple[str, ...]                # checks that separate gold from stale after the change
    gold: dict = field(default_factory=dict)
    stale: dict = field(default_factory=dict)
    reason: str = ""


def admit(h: History) -> Admission:
    """Admit a history only if its hidden checks discriminate.

    The gold patch must pass every check in the world after the change. For an
    affected history the stale patch must fail at least one check there (those
    checks form the stale probe). For a stable history the stale patch is the
    correct one, so it must pass too.
    """
    world = after_files(h)
    gold_result = run_checks(h, world, gold(h))
    stale_result = run_checks(h, world, stale(h))
    names = set(check_names(h))
    if set(gold_result) != names or any(v != "pass" for v in gold_result.values()):
        return Admission(h, False, (), gold_result, stale_result, "gold patch does not pass every check")
    failing = tuple(sorted(failed_checks(h, stale_result)))
    if h.affected:
        if not failing or not set(failing) <= names:
            return Admission(h, False, (), gold_result, stale_result, "stale patch is not caught")
        return Admission(h, True, failing, gold_result, stale_result)
    if failing:
        return Admission(h, False, (), gold_result, stale_result, "stable history breaks the original solution")
    return Admission(h, True, (), gold_result, stale_result)


# --------------------------------------------------------------------------- #
# Materialization: a real Git repository, its branches and worktrees
# --------------------------------------------------------------------------- #

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=p5@memex.test", "-c", "user.name=p5",
                           "-c", "core.autocrlf=false", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def digest_text(text: str) -> str:
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


def materialize(h: History, base: Path) -> dict:
    """Create the trial repository and everything its change needs.

    Returns the repository path and the change plan the fixture writer applies.
    Branches carry changes that arrive through Git (`merged`, `checkout`), and
    a linked worktree carries the `worktree` change; none of them is visible as
    file content in the trial checkout.
    """
    repo = base / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "config", "core.autocrlf", "false")
    t = h.spec
    initial = dict(before_files(h))
    if h.mechanism == "merged":
        initial[t.dep] = t.dep_old  # the common ancestor; main adds its note below
    _write(repo, initial)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    plan: dict = {"history": h.history_id, "mechanism": h.mechanism, "actions": []}
    if h.mechanism == "merged":
        git(repo, "checkout", "-qb", "feature")
        _write(repo, {t.dep: "# Feature branch note.\n" + t.dep_new})
        git(repo, "commit", "-qam", "feature: change the dependency contract")
        git(repo, "checkout", "-q", "main")
        _write(repo, {t.dep: before_files(h)[t.dep]})
        git(repo, "commit", "-qam", "main: add a review note")
        plan["actions"].append({"kind": "merge", "branch": "feature", "resolution": {t.dep: merged_dep(t)}})
    elif h.mechanism == "checkout":
        git(repo, "checkout", "-qb", "feature")
        _write(repo, {t.dep: t.dep_new})
        git(repo, "commit", "-qam", "feature: change the dependency contract")
        git(repo, "checkout", "-q", "main")
        plan["actions"].append({"kind": "checkout", "branch": "feature"})
    elif h.mechanism == "worktree":
        other = base / "other"
        git(repo, "worktree", "add", "-q", "-b", "other-work", str(other))
        plan["actions"].append({"kind": "other_worktree", "path": str(other), "files": {t.dep: t.dep_new}})
    else:
        before, after = before_files(h), after_files(h)
        changed = {p: c for p, c in after.items() if before.get(p) != c}
        if changed:
            plan["actions"].append({"kind": "write", "files": changed})
    if h.mechanism == "superseded":
        plan["actions"].append({"kind": "supersede", "claim_id": "c-contract", "old_revision": "r1",
                                "new_revision": "r2", "assertion": f"Approved decision D2: {t.new_assertion}",
                                "decisions": after_files(h)["DECISIONS.md"]})
    return {"repo": str(repo), "plan": plan}


# --------------------------------------------------------------------------- #
# Graph seeding: the claims every packet-bearing arm starts from
# --------------------------------------------------------------------------- #

def claim_plan(h: History) -> list[dict]:
    """The evidence and claims for this history, as plain data."""
    t = h.spec
    files = before_files(h)
    evidence, claims = [], []

    def source(path, **extra):
        evidence.append({"evidence_id": f"e-{path}" + (f"#{extra['symbol']}" if "symbol" in extra else ""),
                         "source_kind": "source", "path": path, "content_hash": digest_text(files[path]),
                         **extra})
        return evidence[-1]["evidence_id"]

    m = h.mechanism
    if m == "superseded":
        evidence.append({"evidence_id": "e-approval-d1", "source_kind": "approval", "approver": "maintainer"})
        claims.append({"claim_id": "c-contract", "revision_id": "r1", "authority": "human_approved",
                       "assertion": f"Approved decision D1: {t.assertion}",
                       "support_sets": [["e-approval-d1", source("DECISIONS.md"), source(t.dep)]]})
        return [{"evidence": evidence, "claims": claims}]
    if m == "helper":
        support = [[source(t.dep), source(t.helper)]]
    elif m == "removed":
        support = [[source(t.dep, predicate="symbol_exists", symbol=t.symbol)]]
    elif m == "alternative":
        support = [[source(f"tests/test_{t.dep}")], [source("README.md")]]
    else:
        support = [[source(t.dep)]]
    claims.append({"claim_id": "c-contract", "revision_id": "r1", "authority": "inferred",
                   "assertion": t.assertion, "support_sets": support})
    if m == "governed":
        evidence.append({"evidence_id": "e-approval-rule", "source_kind": "approval", "approver": "maintainer"})
        claims.append({"claim_id": "c-rule", "revision_id": "r1", "authority": "human_approved",
                       "assertion": f"Approved rule: {t.rule}",
                       "support_sets": [["e-approval-rule", source("DECISIONS.md")]]})
    return [{"evidence": evidence, "claims": claims}]


async def seed_graph(h: History, repo: Path, uri: str) -> dict:
    """Index the repository and write this history's evidence and claims."""
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    from memex.context.live import ClaimRevision, EvidenceRef
    from memex.runtime.coordinator import RepositoryIndexer
    from memex.runtime.graph import StructuralGraphStore
    from memex.runtime.supports import ClaimStore
    from memex.runtime.views import discover_repository

    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    try:
        await RepositoryIndexer(registration, StructuralGraphStore(driver)).refresh()
        store = ClaimStore(driver)
        for batch in claim_plan(h):
            for e in batch["evidence"]:
                await store.put_evidence(EvidenceRef(repo_id=registration.repo_id, observed_at=1.0, **e),
                                         allow_approval=True)
            for c in batch["claims"]:
                await store.put_claim(ClaimRevision(
                    repo_id=registration.repo_id, observed_at=1.0,
                    claim_id=c["claim_id"], revision_id=c["revision_id"], authority=c["authority"],
                    assertion=c["assertion"],
                    support_sets=tuple(tuple(s) for s in c["support_sets"])), allow_approval=True)
    finally:
        await driver.close()
    return {"repo_id": registration.repo_id, "worktree_id": registration.worktree_id}


async def apply_supersession(repo: Path, uri: str, action: dict) -> None:
    """The graph half of a `superseded` change: a newer approved revision."""
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    from memex.context.live import ClaimRevision, EvidenceRef
    from memex.runtime.supports import ClaimStore
    from memex.runtime.views import discover_repository

    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    try:
        store = ClaimStore(driver)
        await store.put_evidence(EvidenceRef(evidence_id="e-approval-d2", repo_id=registration.repo_id,
                                             source_kind="approval", approver="maintainer", observed_at=2.0),
                                 allow_approval=True)
        await store.put_evidence(EvidenceRef(evidence_id="e-DECISIONS.md@2", repo_id=registration.repo_id,
                                             source_kind="source", path="DECISIONS.md", observed_at=2.0,
                                             content_hash=digest_text(action["decisions"])), allow_approval=True)
        await store.put_claim(ClaimRevision(
            claim_id=action["claim_id"], revision_id=action["new_revision"], repo_id=registration.repo_id,
            assertion=action["assertion"], authority="human_approved", observed_at=2.0,
            supersedes=(action["old_revision"],),
            support_sets=(("e-approval-d2", "e-DECISIONS.md@2"),)), allow_approval=True)
    finally:
        await driver.close()


def apply_change(repo: Path, plan: dict, *, uri: str | None) -> list[str]:
    """Apply a history's change, as a contributor other than the agent would.

    Called once by the fixture writer process. Returns the actions applied.
    """
    applied = []
    for action in plan["actions"]:
        kind = action["kind"]
        if kind == "write":
            _write(repo, action["files"])
        elif kind == "merge":
            try:
                git(repo, "merge", "--no-edit", action["branch"])
            except subprocess.CalledProcessError:
                _write(repo, action["resolution"])  # the conflict, resolved
                git(repo, "add", *action["resolution"])
                git(repo, "commit", "--no-edit", "-qm", "merge feature, resolving the conflict")
        elif kind == "checkout":
            git(repo, "checkout", "-q", action["branch"])
        elif kind == "other_worktree":
            other = Path(action["path"])
            _write(other, action["files"])
            git(other, "commit", "-qam", "other worktree: change the dependency contract")
        elif kind == "supersede":
            if uri:
                asyncio.run(apply_supersession(repo, uri, action))
        applied.append(kind)
    return applied


def history_by_id(history_id: str, histories=None) -> History:
    for h in histories or DEV_HISTORIES:
        if h.history_id == history_id:
            return h
    raise KeyError(history_id)


def clean_tree(path: Path) -> None:
    """Remove a materialized trial directory, including read-only Git objects."""
    def unlock(function, target, _):
        os.chmod(target, 0o700)
        function(target)
    shutil.rmtree(path, onexc=unlock) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=unlock)
