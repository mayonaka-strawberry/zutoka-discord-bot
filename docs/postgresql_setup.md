# PostgreSQL Setup

The bot stores all player data (profiles, decks, display names, game records,
decision logs, game events) in PostgreSQL. Card definitions remain in
`zutomayo/data/cards.json` and are never stored in the database.

This guide covers a complete from-scratch installation on each platform,
ending with a verified connection. PostgreSQL 16 or newer is recommended; the
instructions below install PostgreSQL 17.

## Windows

```powershell
winget install PostgreSQL.PostgreSQL.17
```

- The installer prompts for a password for the `postgres` superuser — choose
  and record one. If the winget silent install skips the prompt, use the EDB
  graphical installer from https://www.postgresql.org/download/windows/
  instead; it always prompts.
- The `postgresql-x64-17` Windows service is installed and started
  automatically. Verify with:

  ```powershell
  Get-Service postgresql*
  ```

- Add the client tools to PATH for the current user so `psql` works in any
  terminal (open a new terminal afterwards):

  ```powershell
  [Environment]::SetEnvironmentVariable('Path', $env:Path + ';C:\Program Files\PostgreSQL\17\bin', 'User')
  ```

- Connect as superuser (enter the password chosen at install):

  ```powershell
  psql -U postgres -h localhost
  ```

## macOS

```bash
brew install postgresql@17
brew services start postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Homebrew creates a superuser matching the macOS username with no password.
Connect with:

```bash
psql postgres
```

## Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
sudo systemctl status postgresql   # verify it is running
```

Debian/Ubuntu creates a `postgres` system user. Connect as superuser with:

```bash
sudo -u postgres psql
```

Local TCP connections with password authentication work out of the box
(`scram-sha-256` for 127.0.0.1 in the default `pg_hba.conf`). Only if the
database must be reached from another host: edit
`/etc/postgresql/17/main/postgresql.conf` (`listen_addresses`) and
`pg_hba.conf`, then `sudo systemctl restart postgresql`. This is not needed
when the bot and the database share a host.

## Role and databases (all platforms)

Inside `psql` as the superuser:

```sql
CREATE ROLE zutoka_bot WITH LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE zutoka OWNER zutoka_bot;
CREATE DATABASE zutoka_test OWNER zutoka_bot;  -- optional, for integration tests
```

## Bot configuration

Install Python dependencies (now includes `asyncpg`):

```bash
pip install -r requirements.txt
```

Add to `.env`:

```
DATABASE_URL=postgresql://zutoka_bot:choose-a-strong-password@localhost:5432/zutoka
ZUTOKA_TEST_DATABASE_URL=postgresql://zutoka_bot:choose-a-strong-password@localhost:5432/zutoka_test
```

`ZUTOKA_TEST_DATABASE_URL` is only needed to run the PostgreSQL integration
tests; without it those tests are skipped.

## Verification

Check connectivity before the first bot start:

```bash
psql "postgresql://zutoka_bot:choose-a-strong-password@localhost:5432/zutoka" -c "SELECT version();"
```

Tables are created automatically at bot startup. To apply the schema
manually:

```bash
python scripts/apply_schema.py
```

Verify the tables exist:

```bash
psql "postgresql://zutoka_bot:choose-a-strong-password@localhost:5432/zutoka" -c "\dt"
```

## Cutover from the JSON storage (one time)

1. `git pull` the latest `main` from the remote so the JSON data (decks, TCG
   decks, usernames) is current before anything is written to the database.
2. Finish or `/zutomayo end` all in-flight games; stop the bot.
3. Run `python scripts/migrate_json_to_postgresql.py --dry-run`, review the
   per-table counts, then run it again without `--dry-run`.
4. Start the bot; spot-check that `/zutomayo viewdeck` autocomplete shows the
   migrated decks.
5. Archive (do not delete yet) `zutomayo/players/`, `zutomayo/decks/`,
   `zutomayo/decks_tcg/`, and `zutomayo/active_games/`.

Note: player profiles (Elo, win/loss, matchup stats) are intentionally not
migrated — the cutover is a fresh start for player statistics. Decks, TCG
decks, and display names are preserved.
