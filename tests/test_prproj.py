"""Unit tests for projectmanager.prproj. Run with:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import gzip
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectmanager import prproj  # noqa: E402
from projectmanager.prproj import CollectOptions, PremiereProject  # noqa: E402


def media_clip(uid: str, name: str, path: str, objid: int) -> str:
    """A bin item, its master clip and the media file behind it."""
    return f"""
    <ClipProjectItem ObjectUID="pi-{uid}"><ProjectItem><Name>{name}</Name></ProjectItem>
        <MasterClip ObjectURef="mc-{uid}"/></ClipProjectItem>
    <MasterClip ObjectUID="mc-{uid}"><Clips><Clip Index="0" ObjectRef="{objid}"/></Clips><Name>{name}</Name></MasterClip>
    <VideoClip ObjectID="{objid}"><Source ObjectRef="{objid + 1}"/></VideoClip>
    <VideoMediaSource ObjectID="{objid + 1}"><Media ObjectURef="m-{uid}"/></VideoMediaSource>
    <Media ObjectUID="m-{uid}"><AudioStream ObjectRef="{objid + 2}"/><FilePath>{path}</FilePath><Title>{os.path.basename(path)}</Title>
        <RelativePath>.\\{os.path.basename(path)}</RelativePath><ActualMediaFilePath>{path}</ActualMediaFilePath></Media>
    <AudioStream ObjectID="{objid + 2}"><PeakFilePath>{path} 48000.pek</PeakFilePath></AudioStream>"""


def sequence(uid: str, name: str, uses: list[str], objid: int) -> str:
    """A sequence whose single track holds one clip of each master clip in `uses`."""
    items = "".join(f'<TrackItem Index="{i}" ObjectRef="{objid + 10 + i}"/>' for i in range(len(uses)))
    subclips = "".join(
        f'\n    <SubClip ObjectID="{objid + 10 + i}"><MasterClip ObjectURef="{ref}"/></SubClip>'
        for i, ref in enumerate(uses))
    # Premiere cuts the project item name at the last dot; the master clip keeps it whole.
    return f"""
    <ClipProjectItem ObjectUID="pi-{uid}"><ProjectItem><Name>{name.rsplit('.', 1)[0]}</Name></ProjectItem>
        <MasterClip ObjectURef="mc-{uid}"/></ClipProjectItem>
    <MasterClip ObjectUID="mc-{uid}"><Clips><Clip Index="0" ObjectRef="{objid}"/></Clips><Name>{name}</Name></MasterClip>
    <VideoClip ObjectID="{objid}"><Source ObjectRef="{objid + 1}"/></VideoClip>
    <VideoSequenceSource ObjectID="{objid + 1}"><Sequence ObjectURef="s-{uid}"/></VideoSequenceSource>
    <Sequence ObjectUID="s-{uid}"><Name>{name}</Name><Tracks>{items}</Tracks></Sequence>{subclips}"""


def build_project(folder: str) -> str:
    media = {}
    for name in ("aroll.mp4", "music.wav", "unused.mp4"):
        path = os.path.join(folder, "src", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(name.encode() * 1000)
        with open(path + " 48000.pek", "wb") as fh:
            fh.write(b"peak")
        media[name] = path
    offline = os.path.join(folder, "src", "gone.mov")
    os.makedirs(os.path.join(folder, "Demo Masks"))
    with open(os.path.join(folder, "Demo Masks", "abc.prmf"), "wb") as fh:
        fh.write(b"mask")
    xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<PremiereData Version="3">
    <Project ObjectRef="1"/>
    <Project ObjectID="1">
        <Node><Properties>
            <ProjectViewState.List ObjectID="2">
                <ProjectViewStates><ProjectViewState><Second ObjectRef="300"/></ProjectViewState></ProjectViewStates>
                <ProjectViewState ObjectID="300"/>
            </ProjectViewState.List>
            <ExportPreset>&lt;?xml?&gt;&#13;&#10;&lt;A/&gt;</ExportPreset>
            <MZ.PrefixKey.OpenSequenceGuidList.1>s-other</MZ.PrefixKey.OpenSequenceGuidList.1>
            <MZ.PrefixKey.OpenSequenceGuidList.3>s-final</MZ.PrefixKey.OpenSequenceGuidList.3>
            <MZ.PrefixKey.OpenSequenceGuidList.2>s-nest</MZ.PrefixKey.OpenSequenceGuidList.2>
            <project.settings.lastknowngoodprojectpath>\\\\?\\{folder}\\Demo.prproj</project.settings.lastknowngoodprojectpath>
        </Properties></Node>
        <RootProjectItem ObjectURef="root"/>
    </Project>
    <RootProjectItem ObjectUID="root"><ProjectItemContainer><Items>
        <Item Index="0" ObjectURef="bin-aroll"/>
        <Item Index="1" ObjectURef="bin-footage"/>
        <Item Index="2" ObjectURef="bin-seqs"/>
        <Item Index="3" ObjectURef="pi-gone"/>
    </Items></ProjectItemContainer></RootProjectItem>
    <BinProjectItem ObjectUID="bin-aroll"><ProjectItem><Name>Aroll</Name></ProjectItem>
        <ProjectItemContainer><Items>
            <Item Index="0" ObjectURef="pi-aroll"/>
        </Items></ProjectItemContainer></BinProjectItem>
    <BinProjectItem ObjectUID="bin-footage"><ProjectItem><Name>Footage</Name></ProjectItem>
        <ProjectItemContainer><Items>
            <Item Index="0" ObjectURef="pi-music"/>
            <Item Index="1" ObjectURef="pi-unused"/>
        </Items></ProjectItemContainer></BinProjectItem>
    <BinProjectItem ObjectUID="bin-seqs"><ProjectItem><Name>Sequences</Name>
        <project.freeform.view.bin.coordinate>{{"v_":[["pi-nest",[10,10,1]],["pi-other",[234,10,1]]]}}</project.freeform.view.bin.coordinate></ProjectItem>
        <ProjectItemContainer><Items>
            <Item Index="0" ObjectURef="pi-nest"/>
            <Item Index="1" ObjectURef="pi-final"/>
            <Item Index="2" ObjectURef="pi-other"/>
        </Items></ProjectItemContainer></BinProjectItem>
    {media_clip("aroll", "A-roll take 1", media["aroll.mp4"], 10)}
    {media_clip("music", "Music.wav", media["music.wav"], 20)}
    {media_clip("unused", "Unused", media["unused.mp4"], 30)}
    {media_clip("gone", "Gone", offline, 40)}
    {sequence("nest", "Hook 1", ["mc-aroll"], 100)}
    {sequence("final", "Promo_10.26.2026_Final_9X16", ["mc-nest", "mc-music"], 150)}
    {sequence("other", "Other", ["mc-unused", "mc-gone"], 200)}
</PremiereData>"""
    # Premiere puts every top-level object on its own line, indented one tab.
    xml = "\n".join("\t" + line[4:] if line.startswith("    <") else line for line in xml.splitlines())
    path = os.path.join(folder, "Demo.prproj")
    with open(path, "wb") as fh:
        fh.write(gzip.compress(xml.encode("utf-8")))
    return path


class PrprojTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.project = PremiereProject(build_project(self.tmp))
        self.dest = os.path.join(self.tmp, "dest")
        os.makedirs(self.dest)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seq(self, name: str) -> str:
        return next(e.uid for e in self.project.sequences if e.name == name)

    def collect(self, names=None, **options):
        """Copy the named sequences (trimming unused clips unless told not to), or everything."""
        uids = None if names is None else [self.seq(n) for n in names]
        if names is not None:
            options.setdefault("exclude_unused", True)
        plan = self.project.plan(self.dest, CollectOptions(**options), uids)
        self.assertEqual(prproj.execute(plan), [])
        return plan, PremiereProject(plan.out_project)

    def test_sequences_use_the_full_master_clip_name(self):
        names = [(e.bin_path, e.name) for e in self.project.sequences]
        self.assertEqual(names, [("Sequences", "Hook 1"), ("Sequences", "Promo_10.26.2026_Final_9X16"),
                                 ("Sequences", "Other")])

    def test_default_copy_keeps_every_sequence_and_clip(self):
        plan, copy = self.collect()
        self.assertEqual(sorted(e.name for e in copy.entries),
                         sorted(e.name for e in self.project.entries))
        self.assertEqual(len(copy.objects), len(self.project.objects))
        for n in (1, 2, 3):
            self.assertIn(f"OpenSequenceGuidList.{n}>", plan.xml)
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "unused.mp4")))

    def test_unused_clips_dropped_when_asked_with_every_sequence(self):
        plan, copy = self.collect(exclude_unused=True)
        self.assertEqual(len(copy.sequences), 3)
        self.assertEqual(sorted(e.name for e in copy.entries if not e.is_sequence),
                         ["A-roll take 1", "Gone", "Music.wav", "Unused"])  # all used by some sequence

    def test_collect_keeps_nested_sequences_and_used_media_only(self):
        plan, copy = self.collect(["Promo_10.26.2026_Final_9X16"])
        self.assertEqual(plan.kept_sequences, ["Hook 1", "Promo_10.26.2026_Final_9X16"])
        self.assertEqual(sorted(e.name for e in copy.entries if not e.is_sequence),
                         ["A-roll take 1", "Music.wav"])
        self.assertNotIn("m-unused", copy.objects)
        self.assertNotIn("s-other", copy.objects)
        self.assertEqual(plan.missing, [])
        graph = copy.graph
        self.assertEqual(set(graph.objects) - graph.reach(graph.starts), set())

    def test_media_is_copied_and_relinked(self):
        plan, copy = self.collect(["Promo_10.26.2026_Final_9X16"])
        aroll = copy.objects["m-aroll"]
        new = aroll.findtext("ActualMediaFilePath")
        self.assertEqual(new, os.path.join(plan.out_dir, "aroll.mp4"))
        self.assertEqual(aroll.findtext("FilePath"), new)
        self.assertEqual(aroll.findtext("RelativePath"), ".\\aroll.mp4")
        with open(new, "rb") as fh:
            self.assertEqual(fh.read(), b"aroll.mp4" * 1000)
        peak = copy.objects["12"].findtext("PeakFilePath")
        self.assertEqual(peak, "\\\\?\\" + os.path.join(plan.out_dir, "Media Cache", "aroll.mp4 48000.pek"))
        self.assertTrue(os.path.isfile(prproj.fs_path(peak)))
        self.assertTrue(plan.out_dir.endswith("Copied_Demo"))

    def test_project_view_state_ids_are_a_private_scope(self):
        # <Second ObjectRef="300"/> inside the view state must not keep object 300 alive.
        self.project.root.append(prproj.ET.fromstring('<Junk ObjectID="300"/>'))
        self.project.graph.reindex()
        plan = self.project.plan(self.dest, CollectOptions(exclude_unused=True), [self.seq("Hook 1")])
        self.assertNotIn("300", prproj._Graph(plan.root).objects)

    def test_unused_clips_kept_when_not_excluded(self):
        plan, copy = self.collect(["Hook 1"], exclude_unused=False)
        names = sorted(e.name for e in copy.entries)
        self.assertEqual(names, ["A-roll take 1", "Gone", "Hook 1", "Music.wav", "Unused"])
        self.assertEqual(plan.missing, [os.path.join(self.tmp, "src", "gone.mov")])

    def test_rename_media_to_clip_names(self):
        plan, copy = self.collect(["Promo_10.26.2026_Final_9X16"], rename_to_clip_names=True)
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "A-roll take 1.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "Music.wav")))  # no double .wav
        self.assertEqual(copy.objects["m-aroll"].findtext("Title"), "A-roll take 1.mp4")

    def test_conform_files_left_alone_when_not_included(self):
        plan, copy = self.collect(["Hook 1"], include_conform=False)
        self.assertEqual(copy.objects["12"].findtext("PeakFilePath"),
                         os.path.join(self.tmp, "src", "aroll.mp4") + " 48000.pek")
        self.assertFalse(os.path.exists(os.path.join(plan.out_dir, "Media Cache")))

    def test_second_copy_gets_a_new_folder(self):
        first, _ = self.collect(["Hook 1"])
        second, _ = self.collect(["Hook 1"])
        self.assertEqual(os.path.basename(second.out_dir), "Copied_Demo_001")
        self.assertNotEqual(first.out_dir, second.out_dir)

    def test_offline_media_is_reported_and_keeps_its_path(self):
        plan, copy = self.collect(["Other"])
        self.assertEqual(plan.missing, [os.path.join(self.tmp, "src", "gone.mov")])
        self.assertEqual(copy.objects["m-gone"].findtext("FilePath"), os.path.join(self.tmp, "src", "gone.mov"))

    def test_aroll_can_be_left_in_place(self):
        plan, copy = self.collect(["Promo_10.26.2026_Final_9X16"], copy_aroll=False)
        original = os.path.join(self.tmp, "src", "aroll.mp4")
        self.assertEqual([j.src for j in plan.skipped], [original])
        self.assertFalse(os.path.exists(os.path.join(plan.out_dir, "aroll.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "music.wav")))
        aroll = copy.objects["m-aroll"]
        self.assertEqual(aroll.findtext("ActualMediaFilePath"), original)
        self.assertEqual(aroll.findtext("RelativePath"), os.path.join("..", "..", "src", "aroll.mp4"))
        self.assertIn("pi-aroll", copy.objects)  # the clip stays in its bin

    def test_aroll_is_copied_by_default(self):
        plan, _ = self.collect(["Hook 1"])
        self.assertEqual(plan.skipped, [])
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "aroll.mp4")))

    def test_aroll_folder_names(self):
        for path in ("Aroll", "A-roll/Day 1", "Footage/a roll", "C:\\Shoot\\AROLL\\c1.mp4", "A_Roll"):
            self.assertTrue(prproj.is_aroll_folder(path), path)
        for path in ("Broll", "Arrow", "Barroll", "Footage", ""):
            self.assertFalse(prproj.is_aroll_folder(path), path)

    def test_untouched_text_is_written_byte_for_byte(self):
        # Premiere refuses files whose escapes were rewritten (&#10; -> a raw newline).
        plan, copy = self.collect(["Hook 1"])
        self.assertIn("<ExportPreset>&lt;?xml?&gt;&#13;&#10;&lt;A/&gt;</ExportPreset>", plan.xml)
        self.assertIn(self.project.text[:self.project.spans[0][0]], plan.xml)
        self.assertTrue(plan.xml.endswith("</PremiereData>"))
        self.assertIn('<Item Index="0" ObjectURef="pi-nest"/>', plan.xml)
        self.assertNotIn('ObjectURef="pi-final"', plan.xml)

    def test_open_sequence_list_only_names_kept_sequences(self):
        # Premiere calls the project damaged when this list names a missing sequence.
        plan, _ = self.collect(["Promo_10.26.2026_Final_9X16"])
        self.assertNotIn("s-other", plan.xml)
        self.assertIn("<MZ.PrefixKey.OpenSequenceGuidList.1>s-nest</MZ.PrefixKey.OpenSequenceGuidList.1>", plan.xml)
        self.assertIn("<MZ.PrefixKey.OpenSequenceGuidList.2>s-final</MZ.PrefixKey.OpenSequenceGuidList.2>", plan.xml)
        self.assertNotIn("OpenSequenceGuidList.3", plan.xml)

    def test_bin_layout_forgets_dropped_items(self):
        plan, _ = self.collect(["Hook 1"])
        self.assertIn('{"v_":[["pi-nest",[10,10,1]]]}', plan.xml)

    def test_masks_folder_is_copied(self):
        plan, _ = self.collect(["Hook 1"])
        with open(os.path.join(plan.out_dir, "Demo Masks", "abc.prmf"), "rb") as fh:
            self.assertEqual(fh.read(), b"mask")

    def test_zip_holds_the_copied_folder(self):
        plan, _ = self.collect(make_zip=True)
        self.assertEqual(plan.zip_path, plan.out_dir + ".zip")
        self.assertTrue(os.path.isdir(plan.out_dir))  # kept by default
        with zipfile.ZipFile(plan.zip_path) as archive:
            names = archive.namelist()
            self.assertIn("Copied_Demo/Demo.prproj", names)
            self.assertIn("Copied_Demo/Demo Masks/abc.prmf", names)
            self.assertEqual(archive.read("Copied_Demo/aroll.mp4"), b"aroll.mp4" * 1000)
            self.assertEqual(archive.getinfo("Copied_Demo/aroll.mp4").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.getinfo("Copied_Demo/Demo.prproj").compress_type, zipfile.ZIP_DEFLATED)
            with open(plan.out_project, "rb") as fh:
                self.assertEqual(archive.read("Copied_Demo/Demo.prproj"), fh.read())

    def test_zip_only_when_folder_not_kept(self):
        plan = self.project.plan(self.dest, CollectOptions(make_zip=True, keep_folder=False))
        self.assertEqual(prproj.execute(plan), [])
        self.assertFalse(os.path.exists(plan.out_dir))
        self.assertTrue(os.path.isfile(plan.zip_path))
        self.assertEqual(os.listdir(self.dest), ["Copied_Demo.zip"])

    def test_keep_folder_is_ignored_without_zip(self):
        plan, _ = self.collect(keep_folder=False)
        self.assertTrue(os.path.isfile(plan.out_project))
        self.assertIsNone(plan.zip_path)

    def test_existing_zip_moves_the_new_copy_on(self):
        with open(os.path.join(self.dest, "Copied_Demo.zip"), "wb") as fh:
            fh.write(b"old")
        plan, _ = self.collect(make_zip=True)
        self.assertEqual(os.path.basename(plan.zip_path), "Copied_Demo_001.zip")
        with open(os.path.join(self.dest, "Copied_Demo.zip"), "rb") as fh:
            self.assertEqual(fh.read(), b"old")

    def test_cancelled_zip_leaves_folder_and_no_zip(self):
        plan = self.project.plan(self.dest, CollectOptions(make_zip=True, keep_folder=False))
        calls = []

        def cancel_while_zipping():
            calls.append(1)
            return os.path.exists(plan.out_project)  # the copy is done; now zipping
        with self.assertRaises(prproj.Cancelled):
            prproj.execute(plan, cancelled=cancel_while_zipping)
        self.assertTrue(os.path.isdir(plan.out_dir))
        self.assertEqual([n for n in os.listdir(self.dest) if n.endswith((".zip", ".part"))], [])

    def moved_project(self) -> PremiereProject:
        """Build a project in old\\, then move the whole folder to new\\, so every saved path is stale."""
        old, new = os.path.join(self.tmp, "old"), os.path.join(self.tmp, "new")
        build_project(old)
        shutil.move(old, new)
        return PremiereProject(os.path.join(new, "Demo.prproj"))

    def test_moved_project_finds_media_in_its_new_place(self):
        project = self.moved_project()
        plan = project.plan(self.dest, CollectOptions())
        self.assertEqual(plan.missing, [os.path.join(self.tmp, "old", "src", "gone.mov")])  # really gone
        self.assertIn((os.path.join(self.tmp, "old", "src", "aroll.mp4"),
                       os.path.join(self.tmp, "new", "src", "aroll.mp4")), plan.relinked)
        self.assertEqual(prproj.execute(plan), [])
        copy = PremiereProject(plan.out_project)
        self.assertEqual(copy.objects["m-aroll"].findtext("FilePath"), os.path.join(plan.out_dir, "aroll.mp4"))
        self.assertTrue(os.path.isfile(os.path.join(plan.out_dir, "music.wav")))

    def test_moved_project_aroll_points_at_its_new_place(self):
        project = self.moved_project()
        plan = project.plan(self.dest, CollectOptions(copy_aroll=False))
        prproj.execute(plan)
        copy = PremiereProject(plan.out_project)
        self.assertEqual(copy.objects["m-aroll"].findtext("ActualMediaFilePath"),
                         os.path.join(self.tmp, "new", "src", "aroll.mp4"))

    def test_moved_file_found_by_unique_name(self):
        old, new = os.path.join(self.tmp, "old"), os.path.join(self.tmp, "new")
        build_project(old)
        shutil.move(old, new)
        os.makedirs(os.path.join(new, "Footage", "Day 1"))
        shutil.move(os.path.join(new, "src", "music.wav"), os.path.join(new, "Footage", "Day 1", "music.wav"))
        plan = PremiereProject(os.path.join(new, "Demo.prproj")).plan(self.dest, CollectOptions())
        self.assertIn(os.path.join(new, "Footage", "Day 1", "music.wav"), dict(plan.relinked).values())

    def test_project_that_did_not_move_relinks_nothing(self):
        plan, _ = self.collect()
        self.assertEqual(plan.relinked, [])

    def test_same_name_projects_in_one_batch_get_separate_folders(self):
        reserved = set()
        first = self.project.plan(self.dest, CollectOptions(), reserved=reserved)
        reserved.add(os.path.normcase(first.out_dir))
        second = self.project.plan(self.dest, CollectOptions(make_zip=True), reserved=reserved)
        self.assertEqual(os.path.basename(first.out_dir), "Copied_Demo")
        self.assertEqual(os.path.basename(second.out_dir), "Copied_Demo_001")
        self.assertEqual(os.path.basename(second.zip_path), "Copied_Demo_001.zip")

    def test_project_paths_point_at_the_new_project(self):
        plan, copy = self.collect(["Hook 1"])
        text = copy.root.find(".//project.settings.lastknowngoodprojectpath").text
        self.assertEqual(text, "\\\\?\\" + plan.out_project)

    def test_not_a_project(self):
        bad = os.path.join(self.tmp, "bad.prproj")
        with open(bad, "w") as fh:
            fh.write("hello")
        with self.assertRaises(prproj.ProjectError):
            PremiereProject(bad)


if __name__ == "__main__":
    unittest.main()
