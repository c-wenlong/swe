"""Per-topic help text for ``swe providers``."""

from swe.console import c


def print_providers_help() -> None:
    print(
        f"""
  {c('bold', 'swe providers')} — Manage AI provider API keys and metadata

  {c('cyan', 'swe providers list [-d] [--api-keys-dir=DIR] [<filter>]')}
      List registered providers + masked key status (``-`` = no key file),
      AI providers first, then infra providers (deploy/platform keys) and
      service providers (search/scraping/browser APIs), each under their
      own heading. A filter matches the kind too: ``list infra``.

  {c('cyan', 'swe providers info <name|alias> [--api-keys-dir=DIR]')}
      Show full details for one provider, including key status + path

  {c('cyan', 'swe providers add <name> [desc] [--url=URL] [--env=ENV] [--file=NAME] [--kind=inference|infra|services]')}
      Register a provider in {c('bold', '~/.swe/config/providers.json')}.
      ``--kind=infra`` files it with the deploy/platform providers,
      ``--kind=services`` with the agent-facing API services.
      Does not create or touch any key file.

  {c('cyan', 'swe providers remove <name>')}
      Remove a provider from the registry. Does not delete your key file.
      Recorded on a removed list, so it stays gone across upgrades instead
      of being re-added the next time defaults merge in.

{c('bold', 'Key storage')}
  By default, keys live in {c('bold', '~/.swe/secrets/providers/.api_keys')},
  one {c('cyan', 'export OPENAI_API_KEY=...')} line per key. {c('cyan', 'swe init')} moves
  an old {c('bold', '~/.api_keys')} there and leaves a symlink behind.
  Override per-invocation with {c('cyan', '--api-keys-dir=DIR')}: a file like that,
  or a directory with one file per provider named by its slug —
  e.g. {c('cyan', 'DIR/openai')} holding the OpenAI key as a single line.

  {c('bold', 'SWE NEVER stores the key string')} — only the filename pointer
  and provider metadata. Mask format:
    {c('dim', 'long key  (>12 chars):')} {c('bold', 'first8')} + {c('dim', '***')} + {c('bold', 'last4')} + {c('dim', '(len=N)')}
    {c('dim', 'short key (<=12):')}     {c('bold', 'first3')} + {c('dim', '***')} + {c('dim', '(len=N)')}
    {c('dim', 'missing key:')}          {c('bold', '-')}

{c('bold', 'Examples')}
  swe providers list
  swe providers info openai
  swe providers add anthropic "Anthropic Claude" \
      --env=ANTHROPIC_API_KEY \
      --url=https://console.anthropic.com/settings/keys
  swe providers add myprov --env=MY_API_KEY --url=https://example.com
  swe providers add myinfra --kind=infra --env=MY_TOKEN --url=https://example.com
  swe providers remove myprov
"""
    )
