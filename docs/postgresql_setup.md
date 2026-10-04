# PostgreSQL setup

The bot stores all player data in PostgreSQL; card definitions stay in `zutomayo/data/cards.json`. Use PostgreSQL 16 or newer; these steps install 17.

## Install

**Windows**

```powershell
winget install PostgreSQL.PostgreSQL.17
[Environment]::SetEnvironmentVariable('Path', $env:Path + ';C:\Program Files\PostgreSQL\17\bin', 'User')
```

- Choose a password for the `postgres` superuser when the installer asks. If the silent winget install skips the prompt, use the installer from https://www.postgresql.org/download/windows/.
- The `postgresql-x64-17` service starts automatically (`Get-Service postgresql*`).
- Open a new terminal for the PATH change, then connect with `psql -U postgres -h localhost`.

**macOS**

```bash
brew install postgresql@17
brew services start postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
psql postgres
```

**Debian/Ubuntu**

```bash
sudo apt update && sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
sudo systemctl status postgresql   # verify it is running
sudo -u postgres psql
```

Local password logins work out of the box. Only remote access needs `listen_addresses` in `postgresql.conf` and an entry in `pg_hba.conf`, both in `/etc/postgresql/<version>/main/`. Restart PostgreSQL after editing them: `sudo systemctl restart postgresql`.

## Role and database

In `psql` as the superuser:

```sql
CREATE ROLE zutoka_bot WITH LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE zutoka OWNER zutoka_bot;
CREATE DATABASE zutoka_test OWNER zutoka_bot;  -- optional, for the integration tests
```

Add the connection to `.env`:

```
DATABASE_URL=postgresql://zutoka_bot:choose-a-strong-password@localhost:5432/zutoka
```

- Check it with `psql "<DATABASE_URL>" -c "SELECT version();"`.
- The bot creates its tables at startup; `python scripts/apply_schema.py` does the same by hand.
- Check the tables with `psql "<DATABASE_URL>" -c "\dt"`.
- To run the integration tests, set `ZUTOKA_TEST_DATABASE_URL` to the `zutoka_test` database in your shell. Tests do not read `.env`.

## Backup and transfer

Each script reads `DATABASE_URL` from `.env`; `--database-url` overrides it.

| Scripts | Format | Use for |
| --- | --- | --- |
| `dump_database.py`, `restore_database.py` | `pg_dump` custom format | Routine backups. Restoring needs client tools at least as new as the dumping server. |
| `export_database.py`, `import_database.py` | JSON | Moving data between machines or PostgreSQL versions. No client tools needed. |

```bash
python scripts/dump_database.py                          # writes zutoka-<timestamp>.dump
python scripts/dump_database.py --output backups/friday.dump
python scripts/restore_database.py backups/friday.dump   # drops and recreates the dumped tables
python scripts/restore_database.py backups/friday.dump --database-url postgresql://zutoka_bot:...@localhost:5432/zutoka_test

python scripts/export_database.py --output zutoka.json   # default: zutoka-export-<timestamp>.json
python scripts/import_database.py zutoka.json --dry-run  # report counts, then roll back
python scripts/import_database.py zutoka.json            # upsert by primary key
python scripts/import_database.py zutoka.json --replace  # wipe first: exact copy
```

- `--output` does not create missing directories.
- The restore target database must already exist.
- The import applies the schema first and refuses a file from a newer schema version.
- `pg_dump` and `pg_restore` are found through `PGBIN` if set, otherwise PATH, then the default install directory: `C:\Program Files\PostgreSQL\<version>\bin`, `/opt/homebrew/opt/postgresql@<version>/bin`, or `/usr/lib/postgresql/<version>/bin`. If they are installed elsewhere, set `PGBIN` to the directory that contains them.
- To copy dev to production: export on the dev machine, copy the file over, stop the bot, run the import with `--replace`, then start the bot.
- Nothing runs on a schedule. For periodic backups, schedule `dump_database.py` with cron or Task Scheduler.
