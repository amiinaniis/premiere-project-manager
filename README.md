# Project Manager for Premiere Pro

Premiere Pro's **Project Manager → "Collect Files and Copy to New Location"**, as a
standalone Windows app: copy a project and every file it uses to a new folder,
**without opening Premiere**. The new project opens in Premiere already linked to
the copies.

Handy for archiving finished jobs, handing a project to another editor, or moving
work to another drive, one project or a whole batch at a time.

## Features

- **No Premiere needed.** Reads `.prproj` files directly and writes a new project
  that points at the copied media.
- **Batches.** Add or drop several projects. Each one is copied to its own
  `Copied_<Project>` folder with the same options, under one progress bar.
  If one project fails, the rest still run.
- **Everything the project needs.** Media, nested sequences, captions, audio conform
  and peak files, audio and video preview files, and the `<Project> Masks` folder
  (tracked masks).
- **Exclude unused clips** (optional). Drops clips that no sequence uses.
- **Copy A-roll or leave it** (optional). Untick *Copy A-roll Footage* and clips in
  an `Aroll` / `A-roll` / `A roll` bin or folder stay linked to the original files
  instead of being copied. Useful when the camera footage is huge and already
  backed up.
- **Zip it** (optional). Makes `Copied_<Project>.zip`, and can delete the folder
  afterwards so only the zip is left. Video is stored, not recompressed, so zipping
  is fast.
- **Moved projects just work.** If you moved a project folder and Premiere hasn't
  re-saved it yet, the app finds the media at its new location, the way Premiere
  does.
- **Rename media to clip names** (optional), as in Premiere's dialog.
- **Safe.** The original project and media are never changed. Offline media is
  listed before copying, disk space is checked first, and a new run never
  overwrites an earlier copy (`Copied_X`, `Copied_X_001`, ...).

## Download and run

**Portable app (no install, no Python):**
1. Download `ProjectManager_Portable.zip` from the
   [latest release](../../releases/latest).
2. Unzip it anywhere.
3. Run `ProjectManager.exe`.

**From source** (Windows, Python 3.11 or newer, no extra packages):
```bat
git clone https://github.com/amiinaniis/premiere-project-manager.git
cd premiere-project-manager
START_PROJECT_MANAGER.bat
```

## How to use

1. **Add projects.** Click **Add…** and pick one or more `.prproj` files, or drag
   them onto the window. Click a project in the list to see its sequences.
   **Remove** takes the selected one off the list.
2. **Pick options** (see below). The defaults copy everything.
3. **Choose the destination** folder with **Browse…**.
4. Click **Calculate** to see how big the copy will be and whether any media is
   offline (**Missing files…** lists it).
5. Click **OK**. Progress shows which project and file is being copied or zipped;
   **Cancel** stops the batch.
6. When it finishes, **Open folder** shows the result, and **Reset** clears the list
   for the next batch (your destination and options are kept).

Each project ends up as:

```
<Destination>\Copied_<Project>\
    <Project>.prproj          new project, linked to the copies
    <media files>
    <Project> Masks\           tracked-mask data, if the project has any
    Media Cache\               audio conform / peak files
    Adobe Premiere Pro Audio Previews\ ...
```

Open the new `.prproj` in Premiere. No relinking is needed.

### Options

| Option | What it does |
|---|---|
| Exclude Unused Clips | Leaves out clips that are not used in any sequence. All sequences are always copied. |
| Include Audio Conform Files | Copies Premiere's `.pek` / conform cache so audio waveforms show straight away. |
| Include Preview Files | Copies rendered audio/video previews. |
| Rename Media Files to Match Clip Names | Names each copied file after its clip in the Project panel. |
| Copy A-roll Footage | Unticked: clips in an A-roll bin or folder are not copied and stay linked to the originals. |
| Zip the Copied Project | Also writes `Copied_<Project>.zip`. |
| Keep Folder After Zipping | Unticked: deletes the folder once the zip is complete, leaving only the zip. |

## Good to know

- Windows only. Tested with projects from Premiere Pro 2026 (26.x).
- It does what *Collect Files and Copy* does: it copies whole files. *Consolidate and
  Transcode* (trimming clips) is not supported.
- Image sequences: only the files the project references are copied.
- After Effects comps and `.mogrt` files referenced by a project are copied as files.
- Media that was offline in the original stays offline in the copy and is listed
  before you start.

## For developers

- `ProjectManager.py`: the window (Tkinter).
- `projectmanager/prproj.py`: reading, trimming, relinking and writing projects.
  A `.prproj` is gzip-compressed XML. The writer keeps Premiere's original text and
  patches only what changes, because Premiere rejects re-serialised files.
- Run the tests: `python -m unittest discover -s tests -v`
- Build the portable app: `BUILD_PROJECT_MANAGER_EXE.bat` (uses PyInstaller in a
  local virtual environment, and writes `release\ProjectManager_Portable.zip`).
