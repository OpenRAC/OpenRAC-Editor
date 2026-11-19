#!/usr/bin/env python3
"""The level editor's tools: OpenRAC Editor projects of a disc's levels, and the edited levels packed back
for the game. They read the disc with OpenRAC's extractor (openrac_root.py finds it).

  openrac_editor.py projects ISO OUT [--level N ...] an OpenRAC Editor project of editable levels
  openrac_editor.py pack ISO PROJECT OUT             the edited levels, packed for the game
  openrac_editor.py open EDITOR --path PROJECT ...   the editor on a project, its files brought up to date
  openrac_editor.py planet ISO PROJECT OVERRIDES      one level's project packed and added as a new planet
  openrac_editor.py check LEVEL_PROJECT              what of the edits cannot be packed (the disc it was read from)
  openrac_editor.py play LEVEL_PROJECT --packed ...  packed, then the game started on that level

Ratchet & Clank's levels are packed back; Going Commando's, Up Your Arsenal's and Deadlocked's open
to look at and to make texture and model packs from (early access). OUT is a new directory under
OpenRAC's assets/ or build/ (or anywhere, with --anywhere). Never commit or share it: see LEGAL.md.
"""

import argparse
from concurrent.futures import as_completed
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import openrac_root  # noqa: F401  (OpenRAC's extractor on the import path)
from disc import LEVEL_COUNT, Disc
import errors
import extract
from extract import (ROOT, adopt_flat_export, background, export_kind, file_ops, normals, output_path, ours, publish,
                     say_output)
from formats import FormatError
from godot import (PROJECT_STAMP, SCHEMA as GODOT_SCHEMA, LevelWriter, project_files, refresh_editor_files,
                   write_project)
from level import decoded, load_level, load_wad_level
from pack import pack
import progress_line
import wads

# What extract.SubprocessPool may run in this file (worker()).
WORKER_FUNCTIONS = ("export_level", "export_wad_level")


def worker_pool(jobs: int):
    return extract.worker_pool(jobs, Path(__file__).resolve())


def ours_packed(path: Path, version: str) -> None:
    """Refuses to replace a non-empty directory unless it is this version's RAC1 pack."""
    if not path.exists():
        return
    if not path.is_dir():
        raise FormatError(f"{path} is not a packed-level folder; choose another folder", code="pack-folder-not-ours")
    if not any(path.iterdir()):
        return
    try:
        manifest = json.loads((path / "packed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("game") != "rac1" or manifest.get("version") != version:
        raise FormatError(f"{path} holds files that are not a RAC1 {version.upper()} packed-level export; "
                          "choose another folder", code="pack-folder-not-ours")


def level_schema(level_dir: Path) -> int | None:
    """The level.json schema a Godot level was written with, None when it has none."""
    try:
        return json.loads((level_dir / "level.json").read_text(encoding="utf-8")).get("schema")
    except (OSError, ValueError, AttributeError):
        return None


def update_godot(dest: Path, levels: list[int], split: bool, export, schema: int,
                 gameplay_source: str | None = None, carry=None, again: bool = False) -> dict[str, list[int]]:
    """Brings the Godot projects in DEST (an earlier export of this command) up to date, keeping
    every edit: a level of the current SCHEMA is left as it is (its scene, with the player's
    edits, is the one they work on); a missing level is exported; a level an older extractor wrote
    moves to DEST's backups folder, edits and all, and a new one takes its place. EXPORT(out,
    levels) writes those levels as a fresh export into OUT, the way godot() does. The editor's
    scripts (rc1/) and the Collision dock (addons/openrac_collision, enabled in project.godot)
    are refreshed in every project. CARRY(level, old level folder, new level folder, backup) carries
    the player's edits from a replaced level's backup onto its fresh one (carry.py) and returns its
    report. AGAIN: every level there is replaced (backed up, written fresh from the disc and its
    edits carried over), not only an outdated one. Returns the levels "kept", "added" and
    "replaced", the "backup" folder when one was made, and the "carried" reports."""
    place = (lambda i: dest / f"level_{i:02}") if split else (lambda i: dest / "levels" / f"level_{i:02}")
    level_dir = (lambda i: place(i) / "levels" / f"level_{i:02}") if split else place
    report: dict = {"kept": [], "added": [], "replaced": [], "backup": None, "carried": []}

    def whole(i: int) -> bool:
        """The level's scene, and its project (a project per level), are there: one with files
        missing is written again (backed up first, as an outdated one is)."""
        return (level_dir(i) / f"level_{i:02}.tscn").is_file() and (not split or (place(i) / "project.godot").is_file())
    todo = []
    for i in levels:
        if not level_dir(i).is_dir():
            report["added"].append(i)
            todo.append(i)
        elif (again or level_schema(level_dir(i)) != schema or not whole(i) or
              (gameplay_source is not None and
               json.loads((level_dir(i) / "level.json").read_text(encoding="utf-8")).get("gameplay_source") != gameplay_source)):
            report["replaced"].append(i)
            todo.append(i)
        else:
            report["kept"].append(i)
    if todo:
        # The fresh levels are written first: a disc that fails to read, or a stop half way,
        # leaves the project as it was (not without the levels that were being replaced).
        fresh = Path(tempfile.mkdtemp(prefix=f".{dest.name}-", dir=dest.parent))
        try:
            export(fresh, todo)
            if report["replaced"]:
                backup = dest.parent / f"{dest.name}-backups" / time.strftime("%Y%m%d-%H%M%S")
                backup.mkdir(parents=True, exist_ok=True)
                for i in report["replaced"]:
                    file_ops.rename(place(i), backup / f"level_{i:02}")
                report["backup"] = backup
            for i in todo:
                source = fresh / f"level_{i:02}" if split else fresh / "levels" / f"level_{i:02}"
                place(i).parent.mkdir(parents=True, exist_ok=True)
                file_ops.rename(source, place(i))
        finally:
            shutil.rmtree(fresh, ignore_errors=True)
    if carry is not None and report["backup"] is not None:
        for i in progress_line.counted("carry", report["replaced"], lambda i: i):
            old = report["backup"] / f"level_{i:02}"
            old_dir = old / "levels" / f"level_{i:02}" if split else old
            try:
                report["carried"].append(carry(i, old_dir, level_dir(i), report["backup"]))
            except Exception as exc:     # noqa: BLE001 -- the fresh level stays; the player hears why
                report["carried"].append({"level": i, "carried": [], "not_carried": [f"carrying failed: {exc}"],
                                          "mobys": 0, "failed": True, "backup": str(report["backup"])})
    if split and report["backup"] is not None:
        # A level project is a planet too (planet.py): its name and id stay with it.
        import planet
        for i in report["replaced"]:
            planet.carry_settings(report["backup"] / f"level_{i:02}", place(i))
    for project in ([place(i) for i in levels] if split else [dest]):
        if (project / "project.godot").is_file():
            refresh_editor_files(project)
    return report


def carry_edits(disc, survey: dict, level: int, old_dir: Path, new_dir: Path, backup: Path) -> dict:
    """Carries a replaced level's edits from OLD_DIR (its backup) onto NEW_DIR (carry.py): the old
    one was read from the gameplay copy its level.json names (a PAL project from before the 60 Hz
    fix names none: it was read from the PAL copy), the new one from the one this disc plays."""
    import carry
    try:
        stats = json.loads((old_dir / "level.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stats = {}
    source = stats.get("gameplay_source") or ("gameplay_pal" if disc.version.key == "pal" else disc.version.gameplay)
    info = next(entry for entry in survey["levels"] if entry["id"] == level)

    def gameplay(name: str) -> bytes:
        place = info["ranges"][name]
        return decoded(disc.sectors(place["lba"], place["sectors"]))
    project = old_dir.parents[1] if old_dir.parent.name == "levels" else backup
    return carry.carry_project(project, old_dir, new_dir, gameplay(source), gameplay(disc.version.gameplay), backup)


def export_godot(out: Path, levels: list[int], anywhere: bool, split: bool, export,
                 gameplay_source: str | None = None, carry=None, again: bool = False) -> str:
    """The godot command's output, made or updated (update_godot); a sentence saying what was done."""
    dest = output_path(out, anywhere, replace=True)
    ours(dest, "godot")
    if export_kind(dest) is None:
        publish(dest, lambda fresh: export(fresh, levels), anywhere, replace=True)
        return f"OpenRAC Editor project{'s, one per level' if split else ''}: {dest}"
    report = update_godot(dest, levels, split, export, GODOT_SCHEMA, gameplay_source, carry, again)
    for done in report["carried"]:
        # One line per level for the launcher (the launcher hides these lines: launcher/src/lib/toolErrors.ts, MARKERS).
        print(f"openrac-carry: {json.dumps(done, separators=(',', ':'))}", flush=True)
    said = [f"OpenRAC Editor project{'s' if split else ''} in {dest}:"]
    if report["kept"]:
        said.append(f"{len(report['kept'])} level(s) kept as they are, with your edits")
    if report["added"]:
        said.append(f"{len(report['added'])} added")
    if report["replaced"]:
        why = "" if again else " made by an older extractor"
        said.append(f"{len(report['replaced'])}{why} written again; the old ones, with "
                    f"any edits, are in {report['backup']}")
    carried = sum(done["mobys"] for done in report["carried"])
    left = sum(len(done["not_carried"]) for done in report["carried"])
    if report["carried"]:
        said.append(f"{carried} edit(s) carried over" + (f", {left} to redo by hand" if left else ""))
    return " ".join(said[:1]) + " " + "; ".join(said[1:]) + "."


def export_level(iso: Path, info: dict, out: Path, lod: int) -> dict:
    """One level of a Godot project; runs in its own process."""
    with Disc(iso) as disc:
        writer = LevelWriter(out, load_level(disc, info), normals(disc))
        writer.stats["gameplay_source"] = disc.version.gameplay
        writer.stats["version"] = disc.version.key
        return writer.write(lod)


def open_later(iso: Path) -> tuple[Disc, wads.WadDisc]:
    disc = Disc(iso)
    return disc, wads.WadDisc(disc, wads.identify(disc))


def export_wad_level(iso: Path, number: int, out: Path, lod: int) -> dict:
    """One Going Commando, Up Your Arsenal or Deadlocked level of a Godot project; runs in its
    own process."""
    disc, source = open_later(iso)
    with disc:
        return LevelWriter(out, load_wad_level(source, source.level(number)), source.normals()).write(lod)


def godot(iso: Path, survey: dict, levels: list[int], lod: int, jobs: int, out: Path, split: bool = False,
          region: str = "PAL", game: str = "rac1") -> None:
    """Levels are independent, so they are written in parallel. SPLIT: a project per level,
    OUT/level_NN, each with only its own level to import and open. GAME: the disc's game; a later
    game's levels are read by number (wads.WadDisc.level), Ratchet & Clank's by survey."""
    where = {i: out / f"level_{i:02}" if split else out for i in levels}
    if split:
        for i in progress_line.counted("project", levels, lambda i: i):
            write_project(where[i], [i], region, game)
    else:
        progress_line.say("project")
        write_project(out, levels, region, game)
    progress_line.say("read", 0, len(levels))
    with worker_pool(min(jobs, len(levels))) as pool:
        if game == "rac1":
            by_id = {entry["id"]: entry for entry in survey["levels"]}
            running = [pool.submit(export_level, iso, by_id[i], where[i], lod) for i in levels]
        else:
            running = [pool.submit(export_wad_level, iso, i, where[i], lod) for i in levels]
        try:
            for count, done in enumerate(as_completed(running), 1):
                stats = done.result()
                print(f"level {stats['level']:02}: {stats['mesh_instances']} meshes placed, "
                      f"{stats['triangles']} triangles, {stats['textures']} textures", flush=True)
                progress_line.say("read", count, len(running), stats.get("level"))
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise


def later_main(args, disc: Disc, version: wads.Version) -> None:
    """The commands for Going Commando's, Up Your Arsenal's and Deadlocked's discs."""
    source = wads.WadDisc(disc, version)
    survey = source.survey()
    numbers = [level["id"] for level in survey["levels"] if "ranges" in level]
    if args.command == "pack":
        raise FormatError(f"packing levels back is Ratchet & Clank's only for now; {version.game}'s levels "
                          "can be extracted, opened in the editor and used for texture and model packs",
                          code="game-not-packable")
    wanted = sorted(set(args.level or numbers))
    unknown = [n for n in wanted if n not in numbers]
    if unknown:
        raise FormatError(f"{version.game} has no level {unknown[0]}; its levels are {', '.join(map(str, numbers))}",
                          code="no-such-level")
    if args.command == "godot":
        print(export_godot(args.out, wanted, args.anywhere, args.split,
                           lambda out, todo: godot(args.iso, survey, todo, args.terrain_lod, max(1, args.jobs), out,
                                                   args.split, version.region, version.game),
                           again=args.again))
        say_output(args.out.resolve())


def sync_project(project: Path) -> bool:
    """Brings PROJECT's copy of this editor's files (the rc1 scripts, the docks' addons) up to date;
    whether it changed anything. The levels and the player's edits are not touched."""
    if not (project / "project.godot").is_file():
        return False
    stamp = project / PROJECT_STAMP
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == project_files():
        return False
    refresh_editor_files(project)
    return True


def open_editor(argv: list[str]) -> int:
    """`open EDITOR ARGS...`: the project ARGS name with --path brought up to date (sync_project),
    then EDITOR started on its own with ARGS, as the launcher starts it (detached)."""
    if not argv:
        print("usage: openrac_editor.py open EDITOR [--path PROJECT] [ARGS...]", file=sys.stderr)
        return 2
    program, rest = argv[0], argv[1:]
    if "--path" in rest[:-1]:
        project = Path(rest[rest.index("--path") + 1])
        if sync_project(project):
            print(f"brought {project}'s editor files up to date")
    options = {"start_new_session": True} if sys.platform != "win32" else {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    try:
        subprocess.Popen([program, *rest], stdin=subprocess.DEVNULL, **options)
    except OSError as exc:
        print(f"openrac_editor: the editor could not be started ({program}): {exc.strerror or exc}", file=sys.stderr)
        return 1
    return 0


def check_project(project: Path) -> None:
    """Refuses PROJECT unless it is an OpenRAC Editor project, or its folder of per-level projects,
    of Ratchet & Clank's levels (the ones that pack back)."""
    if not (project / "project.godot").is_file() and not list(project.glob("level_*/project.godot")):
        raise FormatError(f"{project} is not an OpenRAC Editor project or its folder of "
                          "per-level projects (read the disc's levels first)", code="no-project")
    for stats in [*project.glob("levels/*/level.json"), *project.glob("level_*/levels/*/level.json")]:
        game = json.loads(stats.read_text(encoding="utf-8")).get("game", "rac1")
        if game != "rac1":
            raise FormatError(f"{project} holds {game}'s levels, which are not packed back yet",
                              code="game-not-packable")


def pack_projects(disc, survey: dict, project: Path, dest: Path, anywhere: bool = True) -> dict:
    """PROJECT's levels packed into DEST, published whole (extract.publish: built beside it, so a
    refused edit never leaves half a pack or replaces the last good one); FormatError
    pack-incomplete, naming each edit, when any edit could not be packed (pack.py lists them in
    changes.txt). Returns {"levels", "mobys", "report", "dest", "moby_names"}."""
    check_project(project)
    ours_packed(dest, disc.version.key)
    result, moby_names = {}, {}

    def build_pack(directory: Path) -> None:
        moby_names.clear()
        levels, mobys = pack(disc, survey, project, directory, moby_names)
        report = (directory / "changes.txt").read_text(encoding="utf-8")
        incomplete = refused_lines(report)
        if incomplete:
            details = "\n".join(f"  {line}" for line in incomplete)
            raise FormatError("Some edits could not be packed:\n" + details, code="pack-incomplete")
        result.update(levels=levels, mobys=mobys, report=report)

    result["dest"] = publish(dest, build_pack, anywhere, replace=True)
    result["moby_names"] = moby_names
    return result


def refused_lines(report: str) -> list[str]:
    """The "not packed: ..." lines of a pack's changes.txt, without that mark."""
    return [line.strip()[len("not packed:"):].strip() for line in report.splitlines()
            if line.lstrip().startswith("not packed:")]


# Where the projects folder remembers the disc it was read from (openrac_editor.py projects writes
# it), so the editor's Check and Play find it without the launcher.
DISC_NOTE = Path(".openrac") / "disc.json"


def remember_disc(projects: Path, iso: Path) -> None:
    note = projects / DISC_NOTE
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(json.dumps({"disc": str(iso.absolute())}) + "\n", encoding="utf-8", newline="\n")


def remembered_disc(level_project: Path) -> Path:
    """The disc LEVEL_PROJECT's folder of projects was read from, or FormatError saying what to do."""
    for folder in (level_project, level_project.parent):
        try:
            disc = Path(json.loads((folder / DISC_NOTE).read_text(encoding="utf-8"))["disc"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if disc.is_file():
            return disc
        raise FormatError(f"the disc these levels were read from is no longer at {disc}: run \"Extract the "
                          "levels\" in the launcher once (your edits are kept)", code="file-missing")
    raise FormatError("the editor does not know your disc yet: run \"Extract the levels\" in the launcher once "
                      "(levels you edited are kept)", code="file-missing")


def level_number(level_project: Path) -> int:
    found = sorted(int(p.name[6:]) for p in level_project.glob("levels/level_*") if p.name[6:].isdigit())
    if len(found) != 1:
        raise FormatError(f"{level_project} is not one level's project", code="no-project")
    return found[0]


def check_or_play(args) -> int:
    """`check LEVEL_PROJECT` and `play LEVEL_PROJECT ...`: every level of the projects folder
    LEVEL_PROJECT is in packed (check: into a folder that is thrown away), each edit that cannot be
    packed said on a line of its own ("openrac-problem: <node>: <why>", which the editor lists and
    selects from); play then starts the game on LEVEL_PROJECT's level with the pack."""
    level_project = args.project.resolve()
    projects = level_project.parent if (level_project / "project.godot").is_file() else level_project
    number = level_number(level_project)
    with Disc(remembered_disc(level_project)) as disc:
        if wads.identify(disc) is not None:
            raise FormatError("Check and Play are for Ratchet & Clank's levels", code="game-not-packable")
        survey = disc.survey()
        try:
            if args.command == "check":
                with tempfile.TemporaryDirectory(prefix=".openrac-check-") as temp:
                    result = pack_projects(disc, survey, projects, Path(temp) / "packed")
            else:
                result = pack_projects(disc, survey, projects, args.packed.resolve())
        except FormatError as exc:
            if getattr(exc, "code", None) != "pack-incomplete":
                raise
            for line in str(exc).splitlines()[1:]:
                print(f"openrac-problem: {line.strip()}", flush=True)
            print("Some edits cannot be packed yet: fix them and check again.")
            return 1
    print(f"openrac-check: {result['levels']} level(s), {result['mobys']} edit(s), nothing refused", flush=True)
    if args.command == "check":
        print("Every edit packs.")
        return 0
    game = args.game.resolve()
    if not game.is_file() and game.with_suffix(".exe").is_file():
        game = game.with_suffix(".exe")
    if not game.is_file():
        raise FormatError(f"no game program at {game}: build the port (or start it from the launcher)",
                          code="file-missing")
    command = [str(game), "--data", str(args.data), "--levels", str(args.levels), "--mods", str(result["dest"]),
               "--level", str(number), "--skip-movies"]
    options = {"start_new_session": True} if sys.platform != "win32" else {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(game.parent), **options)
    print(f"Started the game on level {number:02} with your edits.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    # "godot" was its name before (older launchers still run it).
    export = commands.add_parser("projects", aliases=["godot"])
    export.add_argument("iso", type=Path)
    export.add_argument("out", type=Path)
    export.add_argument("--level", type=int, action="append",
                        help="a level to export (repeatable; default: all)")
    export.add_argument("--jobs", type=int, default=extract.default_jobs(),
                        help="levels to export at once (default: all but a few CPU threads, or OPENRAC_EXTRACT_JOBS)")
    export.add_argument("--terrain-lod", type=int, choices=(0, 2), default=0,
                        help="terrain detail: 0 finest (default), 2 coarsest")
    export.add_argument("--split", action="store_true",
                        help="an OpenRAC Editor project per level (OUT/level_NN): each opens in seconds")
    export.add_argument("--anywhere", action="store_true",
                        help="OUT may be outside this repository (the installed launcher's data folder)")
    export.add_argument("--again", action="store_true",
                        help="export every level again, not only an outdated one: the ones there go to the "
                             "backups folder and their edits are carried onto the fresh ones")
    packing = commands.add_parser("pack")
    packing.add_argument("iso", type=Path)
    packing.add_argument("project", type=Path,
                         help="the OpenRAC Editor project the levels were edited in, or the folder of per-level projects")
    packing.add_argument("out", type=Path, help="where the packed levels go (under assets/ or build/)")
    packing.add_argument("--anywhere", action="store_true",
                         help="OUT may be outside this repository (the installed launcher's data folder)")
    packing.add_argument("--states", type=Path, metavar="FILE",
                         help="the port's states program: refresh the packed levels' moby start states "
                              "(moby_states.py); needs --data and --levels")
    packing.add_argument("--data", type=Path, help="the port's game data, for --states")
    packing.add_argument("--levels", type=Path, help="the port's levels, for --states")
    adding = commands.add_parser("planet", help="pack one level's project and add it as a new planet (planet.py)")
    adding.add_argument("iso", type=Path)
    adding.add_argument("project", type=Path, help="one level's project (a project per level)")
    adding.add_argument("overrides", type=Path, help="the launcher's packs folder (<data>/overrides) to add it to")
    adding.add_argument("--anywhere", action="store_true", help="accepted for the launcher; the planet goes to OVERRIDES")
    for name, about in (("check", "pack every level of a level project's folder into a throwaway folder and "
                                  "list each edit that cannot be packed"),
                        ("play", "pack, then start the game on that level with the pack")):
        sub = commands.add_parser(name, help=about)
        sub.add_argument("project", type=Path, help="one level's project (a project per level)")
        if name == "play":
            sub.add_argument("--packed", type=Path, required=True, help="the packed levels folder (written again)")
            sub.add_argument("--game", type=Path, required=True, help="the game program (openrac-rac1-<version>)")
            sub.add_argument("--data", type=Path, required=True, help="the port's game data")
            sub.add_argument("--levels", type=Path, required=True, help="the port's levels")
    args = parser.parse_args()
    if args.command in ("check", "play"):
        try:
            sys.exit(check_or_play(args))
        except (OSError, FormatError, struct.error) as exc:
            errors.say(exc)
            parser.exit(1, f"openrac_editor: {exc}\n")
    if args.command == "projects":
        args.command = "godot"  # its name inside (the folder mark ours() writes keeps it)
    if args.command == "godot":
        # Whatever the player does comes first; the levels' processes have the rest of the CPU.
        background.lower_priority()
    try:
        with Disc(args.iso) as disc:
            later = wads.identify(disc)
            if later is not None:
                later_main(args, disc, later)
                return
            # Ratchet & Clank: its 19 levels, numbered 0 to 18.
            if getattr(args, "level", None) and any(n not in range(LEVEL_COUNT) for n in args.level):
                raise FormatError(f"Ratchet & Clank's levels are 0 to {LEVEL_COUNT - 1}", code="no-such-level")
            survey = disc.survey()
            if args.command == "godot":
                levels = sorted(set(args.level or range(LEVEL_COUNT)))
                if adopt_flat_export(args.out.resolve(), disc.version.key, "godot"):
                    # Projects made before the per-version folders, edits and all: kept, not
                    # written over.
                    print(f"moved the OpenRAC Editor projects made before into {args.out.resolve()}")
                    return
                print(export_godot(args.out, levels, args.anywhere, args.split,
                                   lambda out, todo: godot(args.iso, survey, todo, args.terrain_lod, max(1, args.jobs),
                                                           out, args.split, disc.version.region),
                                   gameplay_source=disc.version.gameplay if disc.version.key == "pal" else None,
                                   carry=lambda i, old, new, backup: carry_edits(disc, survey, i, old, new, backup),
                                   again=args.again))
                remember_disc(args.out.resolve(), args.iso)
                say_output(args.out.resolve())
            elif args.command == "pack":
                dest = args.out.resolve()
                if adopt_flat_export(args.project.resolve(), disc.version.key, "godot"):
                    print(f"moved the OpenRAC Editor projects made before into {args.project.resolve()}")
                if adopt_flat_export(dest, disc.version.key, "packed"):
                    print(f"moved the packed levels made before into {dest}")
                if not args.anywhere and not any(root in dest.parents for root in (ROOT / "assets", ROOT / "build")):
                    raise FormatError("output must be a directory under this repository's assets/ or build/",
                                      code="folder-not-allowed")
                result = pack_projects(disc, survey, args.project, dest, args.anywhere)
                print(result["report"], end="")
                print(f"Packed {result['levels']} level(s), {result['mobys']} edit(s): {result['dest']}")
                program = args.states if args.states is None or args.states.is_file() else args.states.with_suffix(".exe")
                if program is not None and program.is_file() and args.data and args.levels:
                    import moby_states
                    for line in moby_states.after_pack(program, args.data, args.levels, result["dest"], args.project,
                                                       result["moby_names"]):
                        print(line)
                elif program is not None:
                    print(f"moby start states skipped: no states program at {args.states}, or no --data and --levels")
                say_output(result["dest"])
            elif args.command == "planet":
                import overrides
                import planet
                try:
                    done = planet.add_planet(disc, survey, args.project.resolve(), args.overrides.resolve())
                except overrides.PackError as exc:
                    raise FormatError(str(exc), code=planet.code_of(exc)) from exc
                print(done["report"], end="")
                print(f"Added the planet {done['name']} (level {done['level']:02} as its template) to your mods as "
                      f"{done['installed']}. Turn on Mods and play: board the ship and pick it in the ship's menu.")
                say_output(args.overrides.resolve())
    except (OSError, FormatError, struct.error) as exc:
        errors.say(exc)
        parser.exit(1, f"openrac_editor: {exc}\n")
    except Exception:
        # Not one of the cases above: the launcher says its generic sentence, the log has the trace.
        errors.say(Exception())
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["open"]:
        sys.exit(open_editor(sys.argv[2:]))
    if sys.argv[1:] == ["--worker"]:
        extract.worker({name: globals()[name] for name in WORKER_FUNCTIONS})
    else:
        main()
