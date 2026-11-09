"""Coordinated source versions; generated bundles and independent 4D counters are excluded."""

import argparse
from collections import namedtuple
from pathlib import Path
import plistlib
import re
from xml.parsers.expat import ExpatError


PROJECT = Path("GET TEXT INPUT.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
LOCALIZED = Path("English.lproj/InfoPlist.strings")
SOURCE_PATHS = (PROJECT, PLIST, LOCALIZED)
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
SETTING_COUNT = 6
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
REFERENCES = {f"$({key})": key for key in KEYS}
REFERENCES.update({f"${{{key}}}": key for key in KEYS})
SETTING = re.compile(
    r"(?m)^([ \t]*(MARKETING_VERSION|CURRENT_PROJECT_VERSION)[ \t]*=[ \t]*)([^;\n]*)(;)"
)
TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<block>/\*.*?\*/)|(?P<line>//[^\n]*)|'
    r'(?P<string>"(?:\\.|[^"\\])*")|'
    r'(?P<reference>\$\([A-Za-z_][A-Za-z_0-9]*\)|\$\{[A-Za-z_][A-Za-z_0-9]*\})|'
    r'(?P<bare>[^\s"=;{}(),/]+)|(?P<punct>[^\s])',
    re.DOTALL,
)
PLIST_VERSION = re.compile(
    r"<key>(CFBundleShortVersionString|CFBundleVersion)</key>\s*<string>([^<]*)</string>"
)
INFO_PREFIX = "GET TEXT INPUT version "
INFO_SUFFIX = ", Copyright 2024 miyako."
Value = namedtuple("Value", "value start end")


class UniqueDictionary(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError(f"Duplicate metadata key: {key}")
        super().__setitem__(key, value)


def unquote(value):
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def _decode(data):
    for prefix, encoding in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(prefix):
            return prefix, encoding, data[len(prefix):].decode(encoding)
    return b"", "utf-8", data.decode("utf-8")


def _normalized(data):
    return _decode(data)[2].replace("\r\n", "\n").replace("\r", "\n")


def text(path):
    return _normalized(Path(path).read_bytes())


def _render(data, edits):
    prefix, encoding, decoded = _decode(data)
    endings = re.findall(r"\r\n|\r|\n", decoded)
    updated = decoded.replace("\r\n", "\n").replace("\r", "\n")
    for start, end, replacement in sorted(edits, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    if updated.count("\n") != len(endings):
        raise ValueError("Metadata rewrite changed line structure")
    iterator = iter(endings)
    return prefix + re.sub("\n", lambda _: next(iterator), updated).encode(encoding)


def _tokens(value):
    result = []
    offset = 0
    while offset < len(value):
        token = TOKEN.match(value, offset)
        if token is None or (
            value.startswith("/*", offset) and token.lastgroup != "block"
        ) or (value[offset] == '"' and token.lastgroup != "string"):
            raise ValueError("Unterminated metadata comment or string")
        if token.lastgroup not in ("space", "block", "line"):
            result.append(token)
        offset = token.end()
    return result


def _value(token):
    quoted = token.lastgroup == "string"
    return Value(unquote(token[0]), token.start() + quoted, token.end() - quoted)


def project_settings(project):
    tokens = _tokens(project)
    fields = {key: [] for key in KEYS}
    for index, token in enumerate(tokens[:-1]):
        key = unquote(token[0])
        if key not in fields or tokens[index + 1][0] != "=":
            continue
        if index + 3 >= len(tokens) or tokens[index + 3][0] != ";" \
                or tokens[index + 2].lastgroup not in ("bare", "string", "reference"):
            raise ValueError(f"Unsupported source assignment for {key}")
        fields[key].append(_value(tokens[index + 2]))
    if any(len(values) != SETTING_COUNT for values in fields.values()):
        raise ValueError(f"Expected {SETTING_COUNT} source assignments for each version key")
    return fields


def _localized_fields(value):
    tokens = _tokens(value)
    fields = UniqueDictionary()
    for index in range(0, len(tokens), 4):
        assignment = tokens[index:index + 4]
        if len(assignment) != 4 or assignment[0].lastgroup not in ("bare", "string") \
                or assignment[1][0] != "=" or assignment[2].lastgroup != "string" \
                or assignment[3][0] != ";":
            raise ValueError("Unsupported localized strings serialization")
        fields[unquote(assignment[0][0])] = _value(assignment[2])
    return fields


def _validate_localized(value, version):
    fields = _localized_fields(value)
    expected = {
        "CFBundleShortVersionString": version,
        "CFBundleGetInfoString": INFO_PREFIX + version + INFO_SUFFIX,
    }
    if any(key not in fields or fields[key].value != wanted for key, wanted in expected.items()):
        raise ValueError("Localized display differs from selected source version")
    return fields


def _plist(data):
    try:
        value = plistlib.loads(data, dict_type=UniqueDictionary)
    except (plistlib.InvalidFileException, ExpatError, UnicodeError) as error:
        raise ValueError("Invalid metadata plist serialization") from error
    if not isinstance(value, dict):
        raise ValueError("Metadata plist must be a dictionary")
    return value


def _resolved_version(fields):
    resolved = []
    for index in range(SETTING_COUNT):
        def resolve(key, visiting=()):
            if key in visiting:
                raise ValueError("Source version references are cyclic")
            value = fields[key][index].value
            if value in REFERENCES:
                return resolve(REFERENCES[value], (*visiting, key))
            if not re.fullmatch(VERSION, value):
                raise ValueError(f"{key} must resolve to a strict X.Y.Z")
            return value

        marketing = resolve("MARKETING_VERSION")
        if resolve("CURRENT_PROJECT_VERSION") != marketing:
            raise ValueError("CURRENT_PROJECT_VERSION has drifted from MARKETING_VERSION")
        resolved.append(marketing)
    if len(set(resolved)) != 1:
        raise ValueError("MARKETING_VERSION has drifted across configurations")
    return resolved[0]


def _inspect(documents):
    values = {path: _normalized(data) for path, data in documents.items()}
    fields = project_settings(values[PROJECT])
    version = _resolved_version(fields)
    spans = {path: [] for path in SOURCE_PATHS}
    for settings in fields.values():
        spans[PROJECT].extend(field for field in settings if field.value not in REFERENCES)

    plist = _plist(documents[PLIST])
    if "CFBundleShortVersionString" not in plist:
        raise ValueError("Source CFBundleShortVersionString is required")
    # Mask XML comments without changing offsets; only live string contents are editable.
    xml = re.sub(r"<!--.*?-->", lambda match: re.sub(r"[^\n]", " ", match[0]),
                 values[PLIST], flags=re.DOTALL)
    serialized = UniqueDictionary()
    for match in PLIST_VERSION.finditer(xml):
        serialized[match[1]] = Value(match[2], match.start(2), match.end(2))
    expected_keys = {"CFBundleShortVersionString", "CFBundleVersion"} & plist.keys()
    if set(serialized) != expected_keys:
        raise ValueError("Source plist versions must be editable XML strings")
    for key in expected_keys:
        value = plist[key]
        if not isinstance(value, str) or serialized[key].value != value \
                or (version if value in REFERENCES else value) != version:
            raise ValueError(f"{key} is invalid, unresolved or has drifted")
        if value not in REFERENCES:
            spans[PLIST].append(serialized[key])
    localized = _validate_localized(values[LOCALIZED], version)
    spans[LOCALIZED].append(localized["CFBundleShortVersionString"])
    info = localized["CFBundleGetInfoString"]
    spans[LOCALIZED].append(Value(version, info.start + len(INFO_PREFIX),
                                 info.end - len(INFO_SUFFIX)))
    return version, spans


def _documents(root):
    return {path: (Path(root) / path).read_bytes() for path in SOURCE_PATHS}


def read(root=Path(".")):
    return _inspect(_documents(root))[0]


def bump(mode, root=Path(".")):
    index = ("major", "minor", "patch").index(mode)
    documents = _documents(root)
    current, spans = _inspect(documents)
    parts = list(map(int, current.split(".")))
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    new = ".".join(map(str, parts))
    updates = {
        path: _render(data, [(field.start, field.end, new) for field in spans[path]])
        for path, data in documents.items()
    }
    if _inspect(updates)[0] != new:
        raise ValueError("Bumped source metadata did not validate")
    for path, data in updates.items():
        if data != documents[path]:
            (Path(root) / path).write_bytes(data)
    return new


def verify_bundle(bundle, root=Path(".")):
    version = read(root)
    contents = Path(bundle) / "Contents"
    data = _plist((contents / "Info.plist").read_bytes())
    if data.get("CFBundleShortVersionString") != version:
        raise ValueError("Built bundle display version differs from selected source")
    source_plist = _plist((Path(root) / PLIST).read_bytes())
    if ("CFBundleVersion" in source_plist or "CFBundleVersion" in data) \
            and data.get("CFBundleVersion") != version:
        raise ValueError("Built bundle build version differs from selected source")
    serialized = (contents / "Resources/English.lproj/InfoPlist.strings").read_bytes()
    if serialized.startswith(b"bplist00"):
        localized = _plist(serialized)
    else:
        decoded = _normalized(serialized)
        if decoded.lstrip().startswith(("<?xml", "<plist")):
            localized = _plist(serialized)
        else:
            _validate_localized(decoded, version)
            return
    if localized.get("CFBundleShortVersionString") != version or (
        localized.get("CFBundleGetInfoString") != INFO_PREFIX + version + INFO_SUFFIX
    ):
        raise ValueError("Built localized display differs from selected source")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--bump", choices=("patch", "minor", "major"))
    operation.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle:
        verify_bundle(args.verify_bundle)
    else:
        print(bump(args.bump) if args.bump else read())
