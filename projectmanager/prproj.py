"""Premiere Pro project (.prproj) reader and "Project Manager" collector.

A .prproj is gzip-compressed XML. Every object sits at the top level of
<PremiereData> with an ObjectID (int) or ObjectUID (guid), and objects point at
each other with ObjectRef / ObjectURef attributes. Bins list their children the
same way, and Premiere saves files in which every object is reachable from the
root <Project>.

Collecting a project therefore works like a garbage collector:
  1. drop the project items we do not want from their bins,
  2. keep only the objects still reachable from the root,
  3. copy the media those objects point at and rewrite their paths.
"""
from __future__ import annotations

import copy
import gzip
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Optional

LONG_PREFIX = "\\\\?\\"
AUDIO_PREVIEWS = "Adobe Premiere Pro Audio Previews"
VIDEO_PREVIEWS = "Adobe Premiere Pro Video Previews"
MEDIA_CACHE = "Media Cache"
MEDIA_PATH_TAGS = ("FilePath", "ActualMediaFilePath")
_ABS_PATH = re.compile(r"^(\\\\\?\\)?([A-Za-z]:\\|\\\\)")
_HISTORY = re.compile(r"^MediaFileHistory\d+$")
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_AROLL = re.compile(r"^a[\s_-]?roll\b", re.IGNORECASE)
COPY_CHUNK = 8 * 1024 * 1024
ZIP_DEFLATE = (".prproj", ".prmf", ".pek", ".srt", ".xml", ".txt", ".json")


class ProjectError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class ProjectEntry:
    """A clip or sequence as it appears in the Project panel."""
    uid: str
    name: str
    bin_path: str
    is_sequence: bool
    master_uid: Optional[str]


@dataclass
class CollectOptions:
    exclude_unused: bool = False
    include_conform: bool = True
    include_previews: bool = True
    rename_to_clip_names: bool = False
    copy_aroll: bool = True
    make_zip: bool = False
    keep_folder: bool = True  # only matters with make_zip: False leaves just the zip


@dataclass
class CopyJob:
    src: str
    dst: str
    size: int
    kind: str  # media | conform | preview | masks | aroll (not copied)


@dataclass
class CollectPlan:
    project_name: str
    out_dir: str
    out_project: str
    jobs: list[CopyJob]
    missing: list[str]
    skipped: list[CopyJob]  # A-roll left where it is
    kept_sequences: list[str]
    kept_clips: int
    dropped_items: int
    root: ET.Element = field(repr=False)
    xml: str = field(repr=False, default="")
    zip_path: Optional[str] = None  # Copied_<Project>.zip, made after the copy
    keep_folder: bool = True  # False: the folder is deleted once the zip is written
    relinked: list[tuple[str, str]] = field(default_factory=list)  # (saved path, found at)

    @property
    def total_bytes(self) -> int:
        return sum(j.size for j in self.jobs)

    @property
    def needed_bytes(self) -> int:
        """Disk space the copy needs at its peak; the zip holds a second copy of
        everything, and the folder is only deleted after the zip is written."""
        size = self.total_bytes + len(self.xml)
        return size * 2 if self.zip_path else size


def human_size(size: int) -> str:
    amount = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def fs_path(path: str) -> str:
    """Strip the \\\\?\\ long-path prefix Premiere sometimes writes."""
    return path[len(LONG_PREFIX):] if path.startswith(LONG_PREFIX) else path


def is_file_path(text: Optional[str]) -> bool:
    return bool(text) and bool(_ABS_PATH.match(text.strip()))


def is_aroll_folder(path: str) -> bool:
    """True when any folder in a bin path ("Aroll/Day 1") or file path is an A-roll folder."""
    return any(_AROLL.match(part.strip()) for part in re.split(r"[\\/]", path) if part)


def safe_filename(name: str) -> str:
    name = _BAD_NAME_CHARS.sub("_", name).strip().rstrip(".")
    return name or "clip"


def read_prproj_xml(path: str) -> bytes:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


class PremiereProject:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.name = os.path.splitext(os.path.basename(path))[0]
        try:
            self.text = read_prproj_xml(self.path).decode("utf-8")
            self.root = ET.fromstring(self.text)
        except (OSError, ET.ParseError, EOFError, gzip.BadGzipFile, UnicodeDecodeError) as exc:
            raise ProjectError(f"Could not read the project file:\n{exc}") from exc
        if self.root.tag != "PremiereData":
            raise ProjectError("This is not a Premiere Pro project file.")
        self.spans = _top_level_spans(self.text, len(self.root))
        self.graph = _Graph(self.root)
        self.entries = self._read_entries()

    # ── object graph ─────────────────────────────────────────────────────────
    @property
    def objects(self) -> dict[str, ET.Element]:
        return self.graph.objects

    def reach(self, starts, stop_tags: tuple[str, ...] = ()) -> set[str]:
        return self.graph.reach(starts, stop_tags)

    # ── project panel ────────────────────────────────────────────────────────
    def _children(self, bin_el: ET.Element) -> list[str]:
        items = bin_el.find("ProjectItemContainer/Items")
        if items is None:
            return []
        return [i.get("ObjectURef") or i.get("ObjectRef") for i in items]

    def _read_entries(self) -> list[ProjectEntry]:
        entries: list[ProjectEntry] = []
        root_bin = self.graph.root_bin()

        def walk(bin_key: str, path: str):
            for child in self._children(self.objects[bin_key]):
                el = self.objects.get(child)
                if el is None:
                    continue
                if el.tag == "BinProjectItem":
                    name = el.findtext("ProjectItem/Name") or "Bin"
                    walk(child, f"{path}/{name}" if path else name)
                elif el.tag == "ClipProjectItem":
                    entries.append(self._entry(child, el, path))

        walk(root_bin, "")
        return entries

    def _entry(self, key: str, el: ET.Element, bin_path: str) -> ProjectEntry:
        master = el.find("MasterClip")
        master_uid = (master.get("ObjectURef") or master.get("ObjectRef")) if master is not None else None
        master_el = self.objects.get(master_uid) if master_uid else None
        # The project item name drops anything after the last dot ("Promo_10.26"),
        # the master clip keeps the full name the Project panel shows.
        name = (master_el.findtext("Name") if master_el is not None else None) \
            or el.findtext("ProjectItem/Name") or "Untitled"
        is_seq = False
        if master_uid:
            near = self.reach([master_uid], stop_tags=("Sequence", "Media"))
            is_seq = any(self.objects[k].tag == "Sequence" for k in near)
        return ProjectEntry(key, name, bin_path, is_seq, master_uid)

    @property
    def sequences(self) -> list[ProjectEntry]:
        return [e for e in self.entries if e.is_sequence]

    # ── collecting ───────────────────────────────────────────────────────────
    def plan(self, dest_parent: str, options: CollectOptions,
             sequence_uids: Optional[list[str]] = None,
             reserved: Optional[set[str]] = None) -> CollectPlan:
        """Plan the copy. Every sequence is kept unless `sequence_uids` names some.
        `reserved` holds output folders already promised to other projects in a
        batch, so two projects with the same name get Copied_X and Copied_X_001."""
        if sequence_uids is None:
            sequence_uids = [e.uid for e in self.sequences]
        root = copy.deepcopy(self.root)  # never touch the loaded project
        work = _Graph(root)
        by_uid = {e.uid: e for e in self.entries}

        used = self.reach(sequence_uids)
        keep: set[str] = set()
        for entry in self.entries:
            if entry.uid in sequence_uids or (entry.master_uid in used):
                keep.add(entry.uid)
            elif not options.exclude_unused and not entry.is_sequence:
                keep.add(entry.uid)

        root_bin = work.root_bin()
        self._prune_bins(work, root_bin, keep)
        work.collect_garbage()

        out_dir = _unique_dir(os.path.join(dest_parent, f"Copied_{self.name}"), reserved or set())
        out_project = os.path.join(out_dir, f"{self.name}.prproj")
        clip_names = self._clip_names(keep) if options.rename_to_clip_names else {}
        aroll = set() if options.copy_aroll else self._aroll_media(keep)
        relinker = _Relinker(self, work, out_dir, out_project, options, clip_names, aroll)
        jobs, missing = relinker.run()
        xml = self._render(work)

        kept_seq = [e.name for e in self.entries if e.uid in keep and e.is_sequence]
        return CollectPlan(
            project_name=self.name, out_dir=out_dir, out_project=out_project, jobs=jobs,
            missing=missing, skipped=list(relinker.skipped.values()), kept_sequences=kept_seq,
            kept_clips=sum(1 for u in keep if not by_uid[u].is_sequence),
            dropped_items=len(self.entries) - len(keep), root=root, xml=xml,
            zip_path=out_dir + ".zip" if options.make_zip else None,
            keep_folder=options.keep_folder or not options.make_zip,
            relinked=sorted(relinker.relinked.items()),
        )

    def _render(self, work: "_Graph") -> str:
        """The edited project as text. Premiere's parser is stricter than XML
        (it rejects re-serialised files), so this copies the original text of
        every object that survives and patches only what changed."""
        edited = {el.get("ObjectID") or el.get("ObjectUID"): el for el in work.root}
        out = [self.text[:self.spans[0][0]]]
        for el, (start, end) in zip(self.root, self.spans):
            key = el.get("ObjectID") or el.get("ObjectUID")
            chunk = self.text[start:end]
            if key is None:
                out.append(chunk)  # <Project ObjectRef="1"/>
            elif key in edited:
                out.append(_scrub(_patch(chunk, el, edited[key]), work.objects))
        out.append(self.text[self.spans[-1][1]:])
        return "".join(out)

    def _prune_bins(self, work: "_Graph", bin_key: str, keep: set[str]) -> bool:
        """Remove unwanted items from this bin and its sub-bins. Returns True
        when the bin still holds something."""
        bin_el = work.objects[bin_key]
        items = bin_el.find("ProjectItemContainer/Items")
        if items is None:
            return False
        for item in list(items):
            child = item.get("ObjectURef") or item.get("ObjectRef")
            el = work.objects.get(child)
            if el is not None and el.tag == "BinProjectItem":
                wanted = self._prune_bins(work, child, keep)
            else:
                wanted = child in keep
            if not wanted:
                items.remove(item)
        for index, item in enumerate(items):
            item.set("Index", str(index))
        return len(items) > 0

    def _aroll_media(self, keep: set[str]) -> set[str]:
        """Media objects behind the clips that sit in an A-roll bin."""
        out: set[str] = set()
        for entry in self.entries:
            if entry.uid in keep and not entry.is_sequence and entry.master_uid \
                    and is_aroll_folder(entry.bin_path):
                out |= {k for k in self.reach([entry.master_uid], stop_tags=("Sequence",))
                        if self.objects[k].tag == "Media"}
        return out

    def _clip_names(self, keep: set[str]) -> dict[str, str]:
        """Media object -> the name of the clip that shows it in the Project panel."""
        names: dict[str, str] = {}
        for entry in self.entries:
            if entry.uid not in keep or entry.is_sequence or not entry.master_uid:
                continue
            for key in self.reach([entry.master_uid], stop_tags=("Sequence",)):
                if self.objects[key].tag == "Media":
                    names.setdefault(key, entry.name)
        return names


_TOP_LEVEL = re.compile(r"\n\t<(?![/!?])")
_ITEM_LINE = re.compile(r'[ \t]*<Item\b[^>]*\bObjectU?Ref="([^"]+)"[^>]*/>\r?\n?')


def _top_level_spans(text: str, count: int) -> list[tuple[int, int]]:
    """Where each child of <PremiereData> sits in the file. Premiere indents
    them with exactly one tab; '<' inside text is always escaped."""
    starts = [m.start() + 1 for m in _TOP_LEVEL.finditer(text)]
    end = text.rfind("</PremiereData>")
    while end > 0 and text[end - 1] in "\r\n\t ":
        end -= 1
    if len(starts) != count or end < 0:
        raise ProjectError("This project file is laid out in a way this tool does not know yet.")
    return [(s, e) for s, e in zip(starts, starts[1:] + [end])]


_OPEN_SEQUENCE = re.compile(r"([ \t]*)<MZ\.PrefixKey\.OpenSequenceGuidList\.(\d+)>([^<]*)"
                            r"</MZ\.PrefixKey\.OpenSequenceGuidList\.\2>(\r?\n)?")
_BIN_LAYOUT = re.compile(r"(<project\.freeform\.view\.bin\.coordinate>)([^<]*)(</project\.freeform\.view\.bin\.coordinate>)")


def _scrub(chunk: str, alive: dict) -> str:
    """Remove plain-text mentions of objects that were dropped. They are not
    ObjectRefs, so the garbage collector cannot see them, but Premiere reports
    the project as damaged when an open-sequence entry names a missing sequence."""
    if "OpenSequenceGuidList" in chunk:
        found = list(_OPEN_SEQUENCE.finditer(chunk))
        kept = sorted((int(m.group(2)) for m in found if m.group(3).strip() in alive))
        renumber = {old: new for new, old in enumerate(kept, start=1)}
        for m in reversed(found):
            old = int(m.group(2))
            if old in renumber:
                n = renumber[old]
                line = (f"{m.group(1)}<MZ.PrefixKey.OpenSequenceGuidList.{n}>{m.group(3)}"
                        f"</MZ.PrefixKey.OpenSequenceGuidList.{n}>{m.group(4) or ''}")
            else:
                line = ""
            chunk = chunk[:m.start()] + line + chunk[m.end():]
    if "freeform.view.bin.coordinate" in chunk:
        chunk = _BIN_LAYOUT.sub(lambda m: m.group(1) + _drop_layout(m.group(2), alive) + m.group(3), chunk)
    return chunk


def _drop_layout(value: str, alive: dict) -> str:
    """Bin freeform layout: {"view_": [["<item uid>", [x, y, z]], ...]}."""
    try:
        data = json.loads(value)
    except ValueError:
        return value
    if not isinstance(data, dict):
        return value
    changed = False
    for view, entries in data.items():
        if isinstance(entries, list):
            keep = [e for e in entries if not (isinstance(e, list) and e and isinstance(e[0], str)
                                               and e[0] not in alive)]
            changed |= len(keep) != len(entries)
            data[view] = keep
    return json.dumps(data, separators=(",", ":")) if changed else value


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\r", "&#13;").replace("\n", "&#10;"))


def _patch(chunk: str, before: ET.Element, after: ET.Element) -> str:
    """Bring one object's original text in line with its edited element."""
    if before.find("ProjectItemContainer/Items") is not None:
        return _patch_items(chunk, before.find("ProjectItemContainer/Items"),
                            after.find("ProjectItemContainer/Items"))
    edits: list[tuple[str, int, str]] = []
    seen: dict[str, int] = {}
    for old, new in zip(before.iter(), after.iter()):
        nth = seen.get(old.tag, 0)
        seen[old.tag] = nth + 1
        if (old.text or "") != (new.text or "") and len(old) == 0:
            edits.append((old.tag, nth, new.text or ""))
    for tag, nth, text in reversed(edits):  # back to front keeps offsets valid
        chunk = _replace_text(chunk, tag, nth, text)
    return chunk


def _replace_text(chunk: str, tag: str, nth: int, text: str) -> str:
    opening = list(re.finditer(r"<" + re.escape(tag) + r"(?=[\s/>])[^>]*>", chunk))[nth]
    if opening.group(0).endswith("/>"):
        tag_open = opening.group(0)[:-2].rstrip() + ">"
        return chunk[:opening.start()] + tag_open + _escape(text) + f"</{tag}>" + chunk[opening.end():]
    close = chunk.index(f"</{tag}>", opening.end())
    return chunk[:opening.end()] + _escape(text) + chunk[close:]


def _patch_items(chunk: str, before: ET.Element, after: ET.Element) -> str:
    refs = lambda items: [i.get("ObjectURef") or i.get("ObjectRef") for i in items]
    if refs(before) == refs(after):
        return chunk
    start = chunk.index(">", chunk.index("<Items", chunk.index("<ProjectItemContainer"))) + 1
    end = chunk.index("</Items>", start)
    lines = {m.group(1): m.group(0) for m in _ITEM_LINE.finditer(chunk, start, end)}
    first = _ITEM_LINE.search(chunk, start, end)
    head = chunk[start:first.start()] if first else chunk[start:end]
    tail_start = max((m.end() for m in _ITEM_LINE.finditer(chunk, start, end)), default=end)
    body = "".join(re.sub(r'\bIndex="\d+"', f'Index="{i}"', lines[ref], count=1)
                   for i, ref in enumerate(refs(after)))
    return chunk[:start] + head + body + chunk[tail_start:]


def _outgoing(el: ET.Element) -> list[str]:
    """References from one top-level object. An element nested inside it that
    has its own ObjectID (the Project's ProjectViewState.List) is a private
    scope whose IDs restart at 1, so its references are not followed."""
    out = []
    stack = list(el)
    while stack:
        e = stack.pop()
        ref = e.get("ObjectRef") or e.get("ObjectURef")
        if ref:
            out.append(ref)
        if not (e.get("ObjectID") or e.get("ObjectUID")):
            stack.extend(e)
    return out


class _Graph:
    """The object graph of a project."""

    def __init__(self, root: ET.Element):
        self.root = root
        self.reindex()

    def reindex(self):
        self.objects: dict[str, ET.Element] = {}
        self.refs: dict[str, list[str]] = {}
        self.starts: list[str] = []
        for el in self.root:
            key = el.get("ObjectID") or el.get("ObjectUID")
            out = _outgoing(el)
            if key:
                self.objects[key] = el
                self.refs[key] = out
            else:  # <Project ObjectRef="1"/>, the entry point
                self.starts.extend([el.get("ObjectRef") or el.get("ObjectURef")] + out)

    def root_bin(self) -> str:
        for key, el in self.objects.items():
            if el.tag == "RootProjectItem":
                return key
        raise ProjectError("The project has no root bin.")

    def reach(self, starts, stop_tags: tuple[str, ...] = ()) -> set[str]:
        """Every object reachable from `starts`. Objects whose tag is in
        `stop_tags` are recorded but not walked into."""
        seen: set[str] = set()
        stack = list(starts)
        while stack:
            key = stack.pop()
            if key in seen or key not in self.objects:
                continue
            seen.add(key)
            if self.objects[key].tag not in stop_tags:
                stack.extend(self.refs[key])
        return seen

    def collect_garbage(self):
        """Drop every object the root no longer reaches."""
        self.reindex()  # bins were edited since the last index
        seen = self.reach(self.starts)
        for key, el in list(self.objects.items()):
            if key not in seen:
                self.root.remove(el)
        self.reindex()


class _Locator:
    """Finds media whose saved path is stale because the project was moved,
    the way Premiere does when it opens the project: from the relative path,
    then from where the file sat relative to the old project folder, then by
    a unique file name inside the project folder."""

    LEVELS = 3  # also follow a move of the project's parent folders

    def __init__(self, project_path: str, saved_project_path: Optional[str]):
        self.new_dir = os.path.dirname(os.path.abspath(project_path))
        self.old_dir = os.path.dirname(os.path.normpath(fs_path(saved_project_path))) \
            if is_file_path(saved_project_path) else None
        self._names: Optional[dict[str, list[str]]] = None

    @property
    def moved(self) -> bool:
        return bool(self.old_dir) and os.path.normcase(self.old_dir) != os.path.normcase(self.new_dir)

    def find(self, path: str, relative: Optional[str] = None) -> Optional[str]:
        if os.path.isfile(path):
            return path
        for candidate in self._candidates(path, relative):
            if os.path.isfile(candidate):
                return candidate
        if self.moved:  # last resort, only for a project that really moved
            matches = self._by_name().get(os.path.basename(path).lower(), [])
            if len(matches) == 1:
                return matches[0]
        return None

    def _candidates(self, path: str, relative: Optional[str]):
        # Cross-drive relative paths come out as "..\..\D:\x"; they are not usable.
        if relative and ":" not in relative:
            yield os.path.normpath(os.path.join(self.new_dir, relative))
        if not self.moved:
            return
        old, new = self.old_dir, self.new_dir
        for _ in range(self.LEVELS):
            if os.path.normcase(path).startswith(os.path.normcase(old.rstrip("\\/") + os.sep)):
                yield os.path.join(new, os.path.relpath(path, old))
            old_up, new_up = os.path.dirname(old), os.path.dirname(new)
            if old_up == old or new_up == new:
                break
            old, new = old_up, new_up

    def _by_name(self) -> dict[str, list[str]]:
        if self._names is None:
            self._names = {}
            for dirpath, dirs, files in os.walk(self.new_dir):
                dirs[:] = [d for d in dirs if not d.startswith("Adobe Premiere Pro")]
                for name in files:
                    self._names.setdefault(name.lower(), []).append(os.path.join(dirpath, name))
        return self._names


class _Relinker:
    """Decides where every file goes and rewrites the paths in the copy."""

    def __init__(self, project: PremiereProject, work: _Graph, out_dir: str, out_project: str,
                 options: CollectOptions, clip_names: dict[str, str], aroll: set[str]):
        self.project = project
        self.work = work
        self.out_dir = out_dir
        self.out_project = out_project
        self.options = options
        self.clip_names = clip_names
        self.aroll = aroll
        self.skipped: dict[str, CopyJob] = {}
        self.src_dir = os.path.dirname(project.path)
        self.jobs: dict[str, CopyJob] = {}
        self.taken: set[str] = set()
        self.missing: list[str] = []
        self.relinked: dict[str, str] = {}  # stale saved path -> where the file is now
        saved = project.root.findtext(".//project.settings.lastknowngoodprojectpath")
        self.locator = _Locator(project.path, saved)

    def _locate(self, source: str, relative: Optional[str] = None) -> tuple[str, Optional[str]]:
        """(saved path, current path or None when the file cannot be found)."""
        src = os.path.normpath(fs_path(source.strip()))
        found = self.locator.find(src, relative)
        if found and os.path.normcase(found) != os.path.normcase(src):
            self.relinked[src] = found
        return src, found

    def run(self) -> tuple[list[CopyJob], list[str]]:
        for key, el in self.work.objects.items():
            if el.tag == "Media":
                self._media(key, el)
            elif el.tag == "AudioPreviewItem":
                self._simple(el, ("FilePath",), "preview")
            elif el.tag == "AudioStream":
                self._simple(el, ("ConformedAudioPath", "PeakFilePath"), "conform")
        self._masks()
        for el in self.work.root.iter():
            if el.tag == "project.settings.lastknowngoodprojectpath":
                el.text = LONG_PREFIX + self.out_project
            elif el.tag == "project.settings.lastknownparentdirectorypathaboveprojectpath":
                el.text = os.path.dirname(self.out_dir)
        return list(self.jobs.values()), sorted(set(self.missing), key=str.lower)

    def _masks(self):
        """Premiere keeps tracked-mask data in "<project> Masks" beside the
        project file and looks for it there; nothing in the XML points at it."""
        folder = os.path.join(self.src_dir, f"{self.project.name} Masks")
        for dirpath, _, files in os.walk(folder):
            for name in files:
                src = os.path.join(dirpath, name)
                dst = os.path.join(self.out_dir, os.path.basename(folder), os.path.relpath(src, folder))
                self.taken.add(os.path.normcase(dst))
                self.jobs[os.path.normcase(src)] = CopyJob(src, dst, os.path.getsize(src), "masks")

    def _media(self, key: str, el: ET.Element):
        source = el.findtext("ActualMediaFilePath") or el.findtext("FilePath")
        if not is_file_path(source):
            return  # generated media: titles, color mattes, captions
        kind = self._kind(source)
        if kind == "preview" and not self.options.include_previews:
            return
        relative = el.findtext("RelativePath")
        if kind == "media" and not self.options.copy_aroll and (
                key in self.aroll or is_aroll_folder(os.path.dirname(fs_path(source)))):
            self._leave(el, source, relative)
            return
        rename = self.clip_names.get(key) if kind == "media" else None
        new = self._place(source, kind, rename, relative)
        if new is None:
            return
        self._relink(el, new, rename)

    def _leave(self, el: ET.Element, source: str, relative: Optional[str]):
        """A-roll stays where it is: the copy points at the file where it is
        now (which may differ from the saved path if the project was moved)."""
        src, found = self._locate(source, relative)
        if found is None:
            self.missing.append(src)
            return
        self.skipped.setdefault(os.path.normcase(found), CopyJob(found, found, os.path.getsize(found), "aroll"))
        for child in el:
            if found != src and (child.tag in MEDIA_PATH_TAGS or _HISTORY.match(child.tag)) \
                    and is_file_path(child.text):
                child.text = found
            elif child.tag == "RelativePath":
                try:
                    child.text = os.path.relpath(found, self.out_dir)
                except ValueError:  # another drive: Premiere uses the full path
                    pass

    def _relink(self, el: ET.Element, new: str, rename: Optional[str]):
        rel = os.path.relpath(new, self.out_dir)
        rel = rel if rel.startswith("..") else ".\\" + rel
        for child in el:
            if child.tag in MEDIA_PATH_TAGS or _HISTORY.match(child.tag):
                if is_file_path(child.text):
                    child.text = new
            elif child.tag == "RelativePath":
                child.text = rel
        if rename:
            title = el.find("Title")
            if title is not None:
                title.text = os.path.basename(new)

    def _simple(self, el: ET.Element, tags: tuple[str, ...], kind: str):
        for tag in tags:
            child = el.find(tag)
            if child is None or not is_file_path(child.text):
                continue
            kind_here = self._kind(child.text) if kind == "preview" else kind
            if kind_here == "preview" and not self.options.include_previews:
                continue
            if kind_here == "conform" and not self.options.include_conform:
                continue
            new = self._place(child.text, kind_here, None)
            if new is not None:
                child.text = LONG_PREFIX + new

    def _kind(self, path: str) -> str:
        parts = [p.lower() for p in fs_path(path).split("\\")]
        if AUDIO_PREVIEWS.lower() in parts or VIDEO_PREVIEWS.lower() in parts:
            return "preview"
        return "media"

    def _place(self, source: str, kind: str, rename: Optional[str],
               relative: Optional[str] = None) -> Optional[str]:
        saved, src = self._locate(source, relative)
        if src is None:
            if kind == "media":  # Premiere rebuilds missing cache and preview files
                self.missing.append(saved)
            return None
        ident = os.path.normcase(src)
        if ident in self.jobs:
            return self.jobs[ident].dst
        if kind == "preview":
            dst = self._preview_target(src)
        elif kind == "conform":
            dst = self._free(os.path.join(self.out_dir, MEDIA_CACHE, os.path.basename(src)))
        else:
            base = os.path.basename(src)
            if rename:
                ext = os.path.splitext(base)[1]
                stem = rename[:-len(ext)] if ext and rename.lower().endswith(ext.lower()) else rename
                base = safe_filename(stem) + ext
            dst = self._free(os.path.join(self.out_dir, base))
        self.jobs[ident] = CopyJob(src, dst, os.path.getsize(src), kind)
        if kind == "preview" and src.lower().endswith(".cfa"):
            self._preview_peak(src, dst)
        return dst

    def _preview_target(self, src: str) -> str:
        """Keep the path below the previews folder, renaming <old>.PRV to <new>.PRV."""
        parts = src.split("\\")
        lower = [p.lower() for p in parts]
        for folder in (AUDIO_PREVIEWS, VIDEO_PREVIEWS):
            if folder.lower() in lower:
                at = lower.index(folder.lower())
                tail = parts[at + 1:]
                if tail and tail[0].lower().endswith(".prv"):
                    tail[0] = f"{self.project.name}.PRV"
                return self._free(os.path.join(self.out_dir, folder, *tail))
        return self._free(os.path.join(self.out_dir, os.path.basename(src)))

    def _preview_peak(self, src: str, dst: str):
        stem = os.path.splitext(os.path.basename(src))[0]
        peak = os.path.join(os.path.dirname(src), "Peak Files", stem + ".pek")
        if os.path.isfile(peak) and os.path.normcase(peak) not in self.jobs:
            target = os.path.join(os.path.dirname(dst), "Peak Files",
                                  os.path.splitext(os.path.basename(dst))[0] + ".pek")
            self.taken.add(os.path.normcase(target))
            self.jobs[os.path.normcase(peak)] = CopyJob(peak, target, os.path.getsize(peak), "preview")

    def _free(self, path: str) -> str:
        """A destination no other file has claimed: name.ext, name_001.ext, ..."""
        stem, ext = os.path.splitext(path)
        candidate, n = path, 0
        while os.path.normcase(candidate) in self.taken:
            n += 1
            candidate = f"{stem}_{n:03d}{ext}"
        self.taken.add(os.path.normcase(candidate))
        return candidate


def _unique_dir(path: str, reserved: set[str] = frozenset()) -> str:
    """Copied_X, else Copied_X_001, ...; the folder's .zip must be free too,
    and so must any name in `reserved` (normcased)."""
    candidate, n = path, 0
    while (os.path.exists(candidate) or os.path.exists(candidate + ".zip")
           or os.path.normcase(candidate) in reserved):
        n += 1
        candidate = f"{path}_{n:03d}"
    return candidate


def write_project(xml: str, path: str):
    data = gzip.compress(xml.encode("utf-8"))
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def execute(plan: CollectPlan,
            progress: Optional[Callable[[int, int, str], None]] = None,
            cancelled: Callable[[], bool] = lambda: False) -> list[str]:
    """Copy every file in the plan, write the new project, then zip the
    folder if asked. Returns errors."""
    os.makedirs(plan.out_dir, exist_ok=True)
    # Progress runs over the copy and then the zip, which reads it all again.
    total, done, errors = plan.total_bytes * (2 if plan.zip_path else 1), 0, []
    for job in plan.jobs:
        if progress:
            progress(done, total, job.src)
        try:
            os.makedirs(os.path.dirname(job.dst), exist_ok=True)
            done = _copy(job, done, total, progress, cancelled)
        except Cancelled:
            raise
        except OSError as exc:
            errors.append(f"{job.src}: {exc}")
            done += job.size
    write_project(plan.xml, plan.out_project)
    if plan.zip_path:
        _zip_folder(plan.out_dir, plan.zip_path, done, total, progress, cancelled)
        if not plan.keep_folder:
            shutil.rmtree(plan.out_dir)  # made by this run, and the zip is complete
    if progress:
        progress(total, total, plan.zip_path or plan.out_project)
    return errors


def _zip_folder(folder: str, zip_path: str, done: int, total: int, progress, cancelled):
    """Zip `folder` as one top-level folder. Media is already compressed, so it
    is stored as is; deflating gigabytes of video would take long for nothing."""
    tmp = zip_path + ".part"
    top = os.path.basename(folder)
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as archive:
            for dirpath, _, files in os.walk(folder):
                for name in sorted(files):
                    path = os.path.join(dirpath, name)
                    info = zipfile.ZipInfo.from_file(path, os.path.join(top, os.path.relpath(path, folder)))
                    info.compress_type = (zipfile.ZIP_DEFLATED if name.lower().endswith(ZIP_DEFLATE)
                                          else zipfile.ZIP_STORED)
                    with open(path, "rb") as src, archive.open(info, "w", force_zip64=True) as dst:
                        while True:
                            if cancelled():
                                raise Cancelled()
                            chunk = src.read(COPY_CHUNK)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done = min(done + len(chunk), total)
                            if progress:
                                progress(done, total, path)
        os.replace(tmp, zip_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _copy(job: CopyJob, done: int, total: int, progress, cancelled) -> int:
    tmp = job.dst + ".part"
    try:
        with open(job.src, "rb") as src, open(tmp, "wb") as dst:
            while True:
                if cancelled():
                    raise Cancelled()
                chunk = src.read(COPY_CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, job.src)
        shutil.copystat(job.src, tmp)
        os.replace(tmp, job.dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return done
