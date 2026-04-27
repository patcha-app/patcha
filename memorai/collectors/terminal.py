import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from memorai.db.models import Event, EventType, TerminalCommand


class TerminalCollector:
    def __init__(self):
        self.shell = os.getenv("SHELL", "/bin/bash")

    def collect_bash_history(self, since: Optional[datetime] = None) -> List[Event]:
        events = []
        history_file = Path.home() / ".bash_history"

        if not history_file.exists():
            return events

        try:
            # Get file modification time to determine if any recent activity exists
            file_stat = history_file.stat()
            file_mtime = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)

            # If file wasn't modified since the 'since' timestamp, no new commands
            if since and file_mtime < since:
                return events

            with open(history_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            # If 'since' is provided, we'll only collect a limited number of recent commands
            # as a heuristic since bash history doesn't have timestamps
            if since:
                # Only take the last 10 commands as a reasonable assumption for recent activity
                lines = lines[-10:] if len(lines) > 10 else lines

            for line in lines:
                command = line.strip()
                if not command or command.startswith("#"):
                    continue

                # Use file modification time as the best timestamp estimate we have
                timestamp = file_mtime if since else datetime.now(timezone.utc)

                terminal_cmd = TerminalCommand(
                    command=command,
                    timestamp=timestamp,
                    working_dir=str(Path.cwd())
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.TERMINAL,
                    source="bash",
                    raw_content=json.dumps(terminal_cmd.model_dump(), default=str),
                    metadata={
                        "shell": "bash",
                        "command_length": len(command),
                        "note": "bash_history_no_timestamps_recent_only" if since else "bash_history_no_timestamps"
                    }
                )
                events.append(event)

        except Exception as e:
            print(f"Error collecting bash history: {e}")

        return events

    def collect_zsh_history(self, since: Optional[datetime] = None) -> List[Event]:
        events = []
        history_file = Path.home() / ".zsh_history"

        if not history_file.exists():
            return events

        try:
            with open(history_file, "rb") as f:
                content = f.read()

            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                content = content.decode("utf-8", errors="ignore")

            lines = content.split("\n")

            for line in lines:
                if not line.strip():
                    continue

                # Only process lines that start with ": " (proper zsh history format)
                if line.startswith(": "):
                    parts = line.split(";", 1)
                    if len(parts) == 2:
                        timestamp_part = parts[0][2:].split(":")[0]
                        command = parts[1]
                        try:
                            timestamp = datetime.fromtimestamp(int(timestamp_part), tz=timezone.utc)
                        except:
                            # If timestamp parsing fails, skip this entry
                            continue
                    else:
                        continue
                else:
                    # Skip malformed entries that don't have proper zsh timestamp format
                    continue

                if since and timestamp < since:
                    continue

                terminal_cmd = TerminalCommand(
                    command=command.strip(),
                    timestamp=timestamp,
                    working_dir=str(Path.cwd())
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.TERMINAL,
                    source="zsh",
                    raw_content=json.dumps(terminal_cmd.model_dump(), default=str),
                    metadata={
                        "shell": "zsh",
                        "command_length": len(command)
                    }
                )
                events.append(event)

        except Exception as e:
            print(f"Error collecting zsh history: {e}")

        return events

    def collect_fish_history(self, since: Optional[datetime] = None) -> List[Event]:
        events = []
        history_file = Path.home() / ".local/share/fish/fish_history"

        if not history_file.exists():
            return events

        try:
            with open(history_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            entries = content.split("- cmd: ")

            for entry in entries[1:]:
                lines = entry.strip().split("\n")
                command = lines[0]
                timestamp = datetime.now(timezone.utc)

                for line in lines[1:]:
                    if line.strip().startswith("when: "):
                        try:
                            timestamp_str = line.strip().split("when: ")[1]
                            timestamp = datetime.fromtimestamp(int(timestamp_str), tz=timezone.utc)
                        except:
                            pass
                        break

                if since and timestamp < since:
                    continue

                terminal_cmd = TerminalCommand(
                    command=command.strip(),
                    timestamp=timestamp,
                    working_dir=str(Path.cwd())
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.TERMINAL,
                    source="fish",
                    raw_content=json.dumps(terminal_cmd.model_dump(), default=str),
                    metadata={
                        "shell": "fish",
                        "command_length": len(command)
                    }
                )
                events.append(event)

        except Exception as e:
            print(f"Error collecting fish history: {e}")

        return events

    def collect_all(self, since: Optional[datetime] = None) -> List[Event]:
        events = []
        events.extend(self.collect_bash_history(since))
        events.extend(self.collect_zsh_history(since))
        events.extend(self.collect_fish_history(since))
        return sorted(events, key=lambda x: x.timestamp, reverse=True)
