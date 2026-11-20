# Private local runtime data

The first launch creates `config.json` (contains the bot token), `state.json`
(delivery cursor/Telegram offset) and `report.json` (compact public report cache).

Never share this directory, upload it, commit it, or include it in a ZIP.
Do not edit these files while the bot is running. Keep a private backup if needed.
The package builder includes this README only, never any runtime data.
