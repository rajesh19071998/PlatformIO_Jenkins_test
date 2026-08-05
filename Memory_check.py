Import("env")
import subprocess
import os
from datetime import datetime

def human_readable(n):
    try:
        n = float(n)
    except Exception:
        return ""
    if n >= 1024 * 1024:
        return f"{n / (1024*1024):.2f}MB"
    if n >= 1024:
        return f"{n / 1024:.2f}KB"
    return f"{int(n)}B"


def round_up(x, base):
    return int(((x + base - 1) // base) * base)


def log_size(source, target, env):
    elf_file = target[0].get_abspath()

    # Use OBJSIZE if available, fallback to 'size'
    size_tool = env.get("OBJSIZE", "size")

    try:
        result = subprocess.run([size_tool, elf_file], capture_output=True, text=True)
    except Exception:
        return

    lines = result.stdout.strip().splitlines()
    # Choose the correct line from `size` output by matching the ELF basename if possible
    values = None
    if len(lines) >= 2:
        elf_basename = os.path.basename(elf_file)
        for l in lines[1:]:
            parts = l.split()
            if not parts:
                continue
            fname = parts[-1]
            if elf_basename == os.path.basename(fname) or os.path.splitext(os.path.basename(fname))[0] == os.path.splitext(elf_basename)[0]:
                values = parts
                break
        # fallback to second line if no match
        if values is None:
            values = lines[1].split()

    if values:
        # Defensive parsing: take first 5 numeric fields, rest is filename
        text, data, bss, dec, hexval = values[:5]
        filename = " ".join(values[5:])  # handles paths with spaces

        # Convert to integers
        try:
            text_i = int(text)
            data_i = int(data)
            bss_i = int(bss)
        except Exception:
            # If parsing fails, fall back to zeros
            text_i = data_i = bss_i = 0

        # Calculate used sizes
        ram_used = data_i + bss_i

        # Prefer binary (.bin) size for flash_used if available (stronger indicator of actual flash payload)
        flash_used = text_i + data_i
        try:
            build_dir = env.get('BUILD_DIR') or env.subst('$BUILD_DIR')
        except Exception:
            build_dir = None

        bin_size = 0
        if build_dir:
            try:
                candidates = []
                elf_stem = os.path.splitext(os.path.basename(elf_file))[0]
                # prefer exact-matching bin name in top-level build_dir
                try:
                    for name in os.listdir(build_dir):
                        if name.lower().endswith('.bin'):
                            path = os.path.join(build_dir, name)
                            if os.path.splitext(name)[0] == elf_stem or name.lower() == 'firmware.bin':
                                candidates = [path]
                                break
                            candidates.append(path)
                except Exception:
                    pass
                # if none found in top-level, search recursively and prefer exact stem or firmware.bin
                if not candidates:
                    for root, _, files in os.walk(build_dir):
                        for name in files:
                            if name.lower().endswith('.bin'):
                                path = os.path.join(root, name)
                                if os.path.splitext(name)[0] == elf_stem or name.lower() == 'firmware.bin':
                                    candidates = [path]
                                    break
                                candidates.append(path)
                        if candidates and (os.path.splitext(os.path.basename(candidates[0]))[0] == elf_stem or os.path.basename(candidates[0]).lower() == 'firmware.bin'):
                            break
                # choose exact-match or firmware.bin if present, otherwise largest
                if candidates:
                    exact = [p for p in candidates if os.path.splitext(os.path.basename(p))[0] == elf_stem or os.path.basename(p).lower() == 'firmware.bin']
                    if exact:
                        bin_path = exact[0]
                    else:
                        candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
                        bin_path = candidates[0]
                    bin_size = os.path.getsize(bin_path)
            except Exception:
                bin_size = 0

        if bin_size:
            flash_used = bin_size
        else:
            flash_used = text_i + data_i

        # Helper to parse board size values (allow strings like '4MB', ints, or None)
        def parse_size_value(v):
            if v is None:
                return 0
            try:
                # if already int
                if isinstance(v, int):
                    return v
                s = str(v).strip()
                # remove quotes
                s = s.replace('"', '').replace("'", "")
                # common suffixes
                multipliers = {"k": 1024, "kb": 1024, "m": 1024 * 1024, "mb": 1024 * 1024}
                # numeric only
                if s.isdigit():
                    return int(s)
                # endswith suffix
                for suf, mul in multipliers.items():
                    if s.lower().endswith(suf):
                        try:
                            return int(float(s[:-len(suf)]) * mul)
                        except Exception:
                            break
                # try to parse hex
                if s.startswith('0x'):
                    return int(s, 16)
                return int(float(s))
            except Exception:
                return 0

        # Try to obtain flash and ram totals from board config or environment
        flash_total = 0
        ram_total = 0
        try:
            # BoardConfig may be available in env
            board_cfg = env.BoardConfig() if hasattr(env, 'BoardConfig') else None
            if board_cfg:
                # common keys used in PlatformIO board files
                flash_total = board_cfg.get('upload.maximum_size') or board_cfg.get('build.flash_size') or board_cfg.get('upload.maximum_ram_size') or 0
                ram_total = board_cfg.get('build.ram_size') or board_cfg.get('build.sram_size') or board_cfg.get('ram') or 0
        except Exception:
            pass

        # fallback to environment variables
        if not flash_total:
            flash_total = env.get('BOARD_FLASH') or env.get('UPLOAD_MAX_SIZE') or env.get('FLASH_SIZE') or 0
        if not ram_total:
            ram_total = env.get('BOARD_RAM') or env.get('RAM_SIZE') or 0

        flash_total = parse_size_value(flash_total)
        ram_total = parse_size_value(ram_total)

        # Heuristics: if totals still unknown, try to guess from board/PIOENV or round up from usage
        if not flash_total:
            board_name = str(env.get('PIOENV') or env.get('BOARD') or '')
            if 'esp32' in board_name.lower():
                flash_total = 4 * 1024 * 1024
            elif 'esp8266' in board_name.lower() or 'nodemcu' in board_name.lower():
                flash_total = 4 * 1024 * 1024
            else:
                # guess at least double the used flash, round up to nearest 64KB
                flash_total = round_up(max(flash_used * 2, flash_used + 1), 64 * 1024)

        if not ram_total:
            board_name = str(env.get('PIOENV') or env.get('BOARD') or '')
            if 'esp32' in board_name.lower():
                ram_total = 520 * 1024
            elif 'esp8266' in board_name.lower() or 'nodemcu' in board_name.lower():
                ram_total = 160 * 1024
            else:
                # guess double used ram, round up to nearest 4KB
                ram_total = round_up(max(ram_used * 2, ram_used + 1), 4 * 1024)

        # Calculate percentages
        flash_pct = (flash_used / flash_total * 100.0) if flash_total else None
        ram_pct = (ram_used / ram_total * 100.0) if ram_total else None

        # Try to run PlatformIO 'size' target to get the same console summary reported by PlatformIO
        pio_cmd = env.get('PIO', 'pio')
        pioenv = env.get('PIOENV') or env.get('PIOENV') or env.subst('$PIOENV') if hasattr(env, 'subst') else env.get('PIOENV')
        flash_console_used = flash_console_total = None
        ram_console_used = ram_console_total = None
        flash_console_pct = ram_console_pct = None
        try:
            if pioenv:
                pio_proc = subprocess.run([pio_cmd, 'run', '-e', str(pioenv), '-t', 'size'], cwd=env['PROJECT_DIR'], capture_output=True, text=True)
            else:
                pio_proc = subprocess.run([pio_cmd, 'run', '-t', 'size'], cwd=env['PROJECT_DIR'], capture_output=True, text=True)
            out = (pio_proc.stdout or '') + "\n" + (pio_proc.stderr or '')
            # Parse lines like: RAM:   [=         ]  13.5% (used 44092 bytes from 327680 bytes)
            import re
            for line in out.splitlines():
                if 'RAM:' in line:
                    m = re.search(r"used\s+(\d+)\s+bytes\s+from\s+(\d+)\s+bytes", line)
                    if m:
                        ram_console_used = int(m.group(1))
                        ram_console_total = int(m.group(2))
                        # try to capture pct
                        m2 = re.search(r"([0-9]+\.?[0-9]*)%", line)
                        if m2:
                            ram_console_pct = float(m2.group(1))
                if 'Flash:' in line or 'FLASH:' in line or 'Flash' in line:
                    m = re.search(r"used\s+(\d+)\s+bytes\s+from\s+(\d+)\s+bytes", line)
                    if m:
                        flash_console_used = int(m.group(1))
                        flash_console_total = int(m.group(2))
                        m2 = re.search(r"([0-9]+\.?[0-9]*)%", line)
                        if m2:
                            flash_console_pct = float(m2.group(1))
        except Exception:
            pass

        # If console values present, prefer them for flash_used/total and ram_used/total for accuracy display
        if flash_console_used:
            flash_used = flash_console_used
        if flash_console_total:
            flash_total = flash_console_total
        if ram_console_used:
            ram_used = ram_console_used
        if ram_console_total:
            ram_total = ram_console_total

        # Create a git-friendly reports directory in project root
        reports_dir = os.path.join(env["PROJECT_DIR"], "memory_reports")
        try:
            os.makedirs(reports_dir, exist_ok=True)
        except Exception:
            pass

        log_file = os.path.join(reports_dir, "memory_usage.csv")

        header = (
            "timestamp,text,data,bss,dec,hex,filename,"
            "flash_used,flash_total,flash_used_hr,flash_total_hr,flash_pct,"
            "ram_used,ram_total,ram_used_hr,ram_total_hr,ram_pct,"
            "flash_console_used,flash_console_total,flash_console_pct,ram_console_used,ram_console_total,ram_console_pct\n"
        )
        timestamp = datetime.utcnow().isoformat() + 'Z'

        # Format row values, round percentages to 2 decimals when available
        flash_pct_str = f"{flash_pct:.2f}" if flash_pct is not None else ""
        ram_pct_str = f"{ram_pct:.2f}" if ram_pct is not None else ""
        flash_console_pct_str = f"{flash_console_pct:.2f}" if flash_console_pct is not None else ""
        ram_console_pct_str = f"{ram_console_pct:.2f}" if ram_console_pct is not None else ""

        flash_used_hr = human_readable(flash_used)
        flash_total_hr = human_readable(flash_total)
        ram_used_hr = human_readable(ram_used)
        ram_total_hr = human_readable(ram_total)

        row = (
            f"{timestamp},{text_i},{data_i},{bss_i},{dec},{hexval},{os.path.basename(filename)},"
            f"{flash_used},{flash_total},{flash_used_hr},{flash_total_hr},{flash_pct_str},"
            f"{ram_used},{ram_total},{ram_used_hr},{ram_total_hr},{ram_pct_str},"
            f"{flash_console_used or ''},{flash_console_total or ''},{flash_console_pct_str},"
            f"{ram_console_used or ''},{ram_console_total or ''},{ram_console_pct_str}\n"
        )

        if not os.path.exists(log_file):
            with open(log_file, "w") as f:
                f.write(header)
                f.write(row)
        else:
            with open(log_file, "a") as f:
                f.write(row)


# Hook into build
env.AddPostAction("$BUILD_DIR/${PROGNAME}.elf", log_size)
