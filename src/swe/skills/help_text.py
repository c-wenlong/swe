"""Centralized help text for swe skills subcommands."""

from swe.console import c
from swe.paths import SKILL_CATALOGS_FILE, SKILL_LINKS_FILE


def print_skills_overview():
    print(
        f"""
  {c('bold', 'swe skills')} — Discover, list, and manage agent skills

{c('bold', 'List & search')}
  {c('cyan', 'swe skills')}                   This page
  {c('cyan', 'swe skills list')}              Every skill, nested under the root that holds it
  {c('cyan', 'swe skills <filter>')}          Search name, scope or harness: one line per match
  {c('cyan', 'swe skills -d')}                One line per skill, with its description
  {c('cyan', 'swe skills --json')}             Every skill, and which harnesses see it

{c('bold', 'SWE hub')}
  {c('cyan', 'swe skills hub')}               Plugins in ~/.swe/plugins and where they are installed

{c('bold', 'Where skills live')}
  {c('cyan', 'swe find skills -r')}           Every root on disk, and what it links to
  {c('cyan', 'swe find skills -r --scope=all')} Include vendored and project-local roots
  {c('dim', 'swe skills tree and swe skills scope list now forward here.')}

{c('bold', 'Create')}
  {c('cyan', 'swe skills new <name> [--description=…]')} Write a SKILL.md in the hub

{c('bold', 'Layout & symlinks')}
  {c('cyan', 'swe skills link <harness> [target]')}   Link codex/cursor/claude → shared
  {c('cyan', 'swe skills unlink <harness> [--mkdir]')} Break link; optional empty dir
  {c('cyan', 'swe skills move <name> --from=A --to=B')} Move a skill folder
  {c('cyan', 'swe skills adopt [<name>]')}            Pull harness-only skills into shared
  {c('cyan', 'swe skills remove <name>')}             Delete a shared skill and its links

{c('bold', 'Per-skill selections')}
  {c('cyan', 'swe skills enable <name> --harness=<h>')}   Link one shared skill into a harness
  {c('cyan', 'swe skills disable <name> --harness=<h>')}  Drop it without touching the rest
  {c('cyan', 'swe skills sync')}                           Re-apply each selection (new skills arrive)

{c('bold', 'Usage')}
  {c('cyan', 'swe skills usage <name>')}                Call counts from the session index

{c('bold', 'Catalogs (extra skill directories)')}
  {c('cyan', 'swe skills discover [--apply]')} Scan ~/Desktop and ~/Documents
  {c('cyan', 'swe skills catalog .')}         Add current directory as catalog
  {c('cyan', 'swe skills catalog add [path]')} Register a skills directory
  {c('cyan', 'swe skills catalog list')}      List configured catalogs
  {c('cyan', 'swe skills catalog remove <key>')} Remove a catalog entry

{c('bold', 'Config')}
  {SKILL_CATALOGS_FILE}   extra catalog paths
  {SKILL_LINKS_FILE}      recorded harness symlinks

{c('bold', 'Help')}  {c('cyan', 'swe skills help <topic>')}  — topics: adopt, catalog, discover, enable, hub, link, move, new, remove, sync, tree, scope, unlink, usage
  {c('dim', 'help comes before the topic here — unlike swe mcp <command> help, which puts it after.')}
"""
    )


def print_skills_catalog_help():
    print(
        f"""
  {c('bold', 'swe skills catalog')} — Register extra skill directories

  {c('cyan', 'swe skills catalog list')}                 List configured catalogs
  {c('cyan', 'swe skills catalog add [path] [label]')}   Register (default path: .)
  {c('cyan', 'swe skills catalog .')}                    Add current directory
  {c('cyan', 'swe skills catalog remove <label|path>')}  Remove from registry

{c('bold', 'Examples')}
  cd ~/Projects/my-app/skills && swe skills catalog .
  swe skills catalog add ~/Desktop/Projects/ai-engineering/skills
  swe skills catalog add ./skills my-project

{c('bold', 'Config')}  {SKILL_CATALOGS_FILE}
"""
    )


def print_skills_discover_help():
    print(
        f"""
  {c('bold', 'swe skills discover')} — Find skill catalogs on Desktop/Documents

  Scans {c('dim', '~/Desktop')} and {c('dim', '~/Documents')} for folders named {c('dim', 'skills')}
  that contain SKILL.md files (nested catalogs are collapsed to the outermost match).

  {c('cyan', 'swe skills discover')}              Dry-run list
  {c('cyan', 'swe skills discover --apply')}      Register new catalogs
  {c('cyan', 'swe skills discover --json')}       Machine-readable output
  {c('cyan', 'swe skills discover --all')}        Include already-registered catalogs

{c('bold', 'See also')}  {c('cyan', 'swe skills catalog add .')} for manual registration
"""
    )


def print_skills_new_help():
    print(
        f"""
  {c('bold', 'swe skills new')} — Create a SKILL.md in the shared skills root

  {c('cyan', 'swe skills new my-skill')}                        Skeleton at ~/.swe/skills/my-skill/SKILL.md
  {c('cyan', 'swe skills new my-skill --description="Use when: X"')}
  {c('cyan', '--json')}                          Print the result as JSON (errors go to stderr)

{c('bold', 'Name')}  lowercase letters, digits, single hyphens between them (64 chars max)
"""
    )


def print_skills_link_help():
    print(
        f"""
  {c('bold', 'swe skills link')} — Point a harness skills folder at another root

  {c('cyan', 'swe skills link codex')}                 Link codex → shared (default)
  {c('cyan', 'swe skills link codex shared')}           Explicit target
  {c('cyan', 'swe skills link codex claude')}           Link to claude's resolved root
  {c('cyan', 'swe skills link codex --force')}          Replace non-empty directory
  {c('cyan', '--json')}                          Print the result as JSON (errors go to stderr)

{c('bold', 'Harness labels')}  shared, cursor, codex, claude  (or a path)
"""
    )


def print_skills_unlink_help():
    print(
        f"""
  {c('bold', 'swe skills unlink')} — Break a harness symlink for private skills

  {c('cyan', 'swe skills unlink codex')}              Remove codex → shared symlink
  {c('cyan', 'swe skills unlink codex --mkdir')}      Replace with empty directory
  {c('cyan', '--json')}                          Print the result as JSON

  Then move harness-specific skills:
  {c('cyan', 'swe skills move my-skill --from=shared --to=codex')}
"""
    )


def print_skills_move_help():
    print(
        f"""
  {c('bold', 'swe skills move')} — Move a skill folder between scope roots

  {c('cyan', 'swe skills move <name> --from=<scope> --to=<scope>')}

{c('bold', 'Examples')}
  swe skills move query --from=ai-engineering --to=shared
  swe skills move my-skill --from=shared --to=codex

  Scopes are harness labels (shared, codex, claude, cursor), catalog labels,
  or paths registered via {c('cyan', 'swe skills catalog add')}.

  {c('cyan', '--force')}   Back up and replace a skill already at the destination
  {c('cyan', '--json')}    Print the result as JSON

  If both scopes resolve to the same tree (symlinked), the move is refused:
  unlink the destination harness first to give it a private skills folder.
"""
    )


def print_skills_toggle_help():
    print(
        f"""
  {c('bold', 'swe skills enable/disable')} — Turn one shared skill on or off per harness

  {c('cyan', 'swe skills enable my-skill --harness=codex')}    Link it into codex's folder
  {c('cyan', 'swe skills disable my-skill --harness=codex')}   Remove just that link
  {c('cyan', '--json')}                          Print the result as JSON

{c('bold', 'How a folder selects')}
  {c('bold', 'all')}      the whole skills folder is one link to shared (the default)
  {c('bold', 'except')}   a real dir linking every shared skill but the ones disabled —
           disabling in a linked folder converts it to this, and new shared
           skills still arrive
  {c('bold', 'only')}     a real dir linking just the skills enabled — how a private
           skills folder picks shared skills up, one at a time

  Enabling in a folder that has no skills dir yet creates an {c('bold', 'only')} root.
  A private folder with the same name already shadows the shared skill there;
  move it with {c('cyan', 'swe skills move')} first to make the two one.
"""
    )


def print_skills_adopt_help():
    print(
        f"""
  {c('bold', 'swe skills adopt')} — Pull harness-only skills into the shared tree

  {c('cyan', 'swe skills adopt')}               Adopt every harness-only skill
  {c('cyan', 'swe skills adopt --all')}          Same thing, spelled out
  {c('cyan', 'swe skills adopt <name>')}         Adopt one skill by name
  {c('cyan', 'swe skills adopt --dry-run')}      Show what would move, write nothing
  {c('cyan', 'swe skills adopt --json')}         Print the result as JSON

  A skill is harness-only when its folder sits in a harness's real skills
  directory (a root `swe init` reports as {c('bold', 'keep')} or {c('bold', 'selection')})
  instead of ~/.swe/skills. Adopting moves the folder into the shared tree
  verbatim, leaves a symlink behind where it was so the donor sees no
  change, records where it came from in {SKILL_LINKS_FILE}'s
  ``adopted_from``, and marks it off for every other harness so nothing
  new appears anywhere.

  {c('bold', 'Conflicts')} — when two roots hold the same name, the first in
  registry order donates and the rest are reported, never merged. When
  ~/.swe/skills already has the name the private copy stays where it is —
  identical copies fold into links, different ones are reported.

  `swe init` runs this pass too; `swe init --no-adopt` skips it. Plugin
  caches and harness-owned folders (``~/.codex/skills/.system``) are never
  adopted. A moved-out shared skill (`swe skills move --to=<harness>`)
  comes back on the next pass — that is deliberate.
"""
    )


def print_skills_remove_help():
    print(
        f"""
  {c('bold', 'swe skills remove')} — Delete a shared skill and its links

  {c('cyan', 'swe skills remove <name>')}        Back up and delete a shared skill
  {c('cyan', 'swe skills rm <name>')}            Alias
  {c('cyan', 'swe skills delete <name>')}        Alias
  {c('cyan', 'swe skills remove <name> --json')} Print the result as JSON

  Removes the folder under ~/.swe/skills after copying it into
  ~/.swe/backups, drops every per-skill link pointing into it (including
  the link an adopt left in its donor root), and clears its selection and
  provenance records so a later adopt cannot resurrect it. A private
  folder of the same name in a harness root is user content and stays —
  the command says so, since the next adopt pass pulls it back.
"""
    )


def print_skills_sync_help():
    print(
        f"""
  {c('bold', 'swe skills sync')} — Re-apply each harness's skill selection

  Compares every selected skills folder (an {c('bold', 'except')} or {c('bold', 'only')} root)
  to what the shared tree holds now, and fixes the links: skills added to
  shared arrive in except roots, links to deleted skills are dropped, and
  a link removed by hand comes back when its record still wants it.

  {c('cyan', 'swe skills sync')}            Repair every selection root
  {c('cyan', 'swe skills sync --json')}    Print what changed as JSON
"""
    )


def print_skills_usage_help():
    print(
        f"""
  {c('bold', 'swe skills usage')} — How often a skill gets called

  Counts {c('bold', 'Skill: <name>')} tool calls in the session search index
  (~/.swe/data/search/search.sqlite), broken down by harness, with the call
  count, the sessions it ran in, and when it was last used.

  {c('cyan', 'swe skills usage my-skill')}         Counts for one skill
  {c('cyan', 'swe skills usage my-skill --json')}  Machine-readable

  {c('dim', 'Needs the index: `swe session index --on`, or it stays on while the')}
  {c('dim', 'app runs. Its first scan reads existing sessions too; `Skill` calls')}
  {c('dim', 'written before the `skill` input key tally under "unattributed".')}
"""
    )


def print_skills_hub_help():
    print(
        f"""
  {c('bold', 'swe skills hub')} — What the swe hub offers

  Lists every plugin in the marketplaces under {c('dim', '~/.swe/plugins')}, the
  skills each one ships, and whether Claude Code and Codex have it installed.

  {c('cyan', 'swe skills hub')}           One line per plugin
  {c('cyan', 'swe skills hub --json')}    Machine-readable, with the command that installs each

  {c('dim', 'Read-only. swe find plugins --full shows the same gaps across every harness.')}
"""
    )


def print_skills_tree_help():
    print(
        f"""
  {c('bold', 'swe skills tree')} — Symlink layout, now part of swe find

  {c('cyan', 'swe skills tree')}               forwards to {c('cyan', 'swe find skills -r')}
  {c('cyan', 'swe skills scope list')}          forwards to the same view
  {c('cyan', 'swe skills tree --scope=all')}    include vendored and project-local roots

  {c('dim', 'Both spellings stay because they were the original commands; swe find owns the view now.')}
"""
    )


SKILLS_HELP_TOPICS = {
    "": print_skills_overview,
    "adopt": print_skills_adopt_help,
    "catalog": print_skills_catalog_help,
    "catalogs": print_skills_catalog_help,
    "delete": print_skills_remove_help,
    "disable": print_skills_toggle_help,
    "discover": print_skills_discover_help,
    "enable": print_skills_toggle_help,
    "hub": print_skills_hub_help,
    "link": print_skills_link_help,
    "new": print_skills_new_help,
    "remove": print_skills_remove_help,
    "rm": print_skills_remove_help,
    "sync": print_skills_sync_help,
    "unlink": print_skills_unlink_help,
    "usage": print_skills_usage_help,
    "move": print_skills_move_help,
    "tree": print_skills_tree_help,
    "scope": print_skills_tree_help,
    "scopes": print_skills_tree_help,
}


def cmd_skills_help(args: list[str]) -> int:
    topic = args[0].lower() if args else ""
    if topic in ("-h", "--help"):
        topic = ""
    handler = SKILLS_HELP_TOPICS.get(topic)
    if handler is None:
        print(c("red", f"  Unknown skills help topic: {topic!r}"))
        print(c("dim", f"  Topics: {', '.join(sorted(t for t in SKILLS_HELP_TOPICS if t))}"))
        return 1
    handler()
    return 0
