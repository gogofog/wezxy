# -*- coding: utf-8 -*-
"""
Wexxyy Tweaker — оптимизация Windows 10/11.
Запуск:  python WexxyyTweaker.py   (сам запросит права администратора)
Сборка:  build.bat  ->  WexxyyTweaker.exe с иконкой W
"""
import sys, os, json, ctypes, subprocess, threading, queue, struct, zlib, base64
from dataclasses import dataclass, field

try:
    import winreg
except ImportError:          # не Windows (нужно только для --make-icon)
    winreg = None

APP = "Wexxyy Tweaker"
DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "WexxyyTweaker")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
ICO_FILE = os.path.join(DATA_DIR, "W.ico")
NO_WINDOW = 0x08000000


def _say(text):
    """Безопасный вывод: не падает на cp1252-консолях и в --windowed (stdout=None)."""
    try:
        sys.stdout.write(text + "\n")
    except Exception:
        pass

# ───────────────────────── иконка W (без внешних библиотек) ─────────────────────────
def _render_icon(n):
    ss = 3
    pts = [(.20, .27), (.345, .73), (.50, .40), (.655, .73), (.80, .27)]
    segs = list(zip(pts[:-1], pts[1:]))
    hw, rad = 0.058, 0.22
    c1, c2 = (150, 112, 255), (70, 52, 225)

    def dist(px, py, a, b):
        ax, ay = a; bx, by = b
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        qx, qy = ax + t * dx, ay + t * dy
        return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5

    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            R = G = B = cnt = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = (x + (sx + .5) / ss) / n
                    v = (y + (sy + .5) / ss) / n
                    ddx = max(abs(u - .5) - (.5 - rad), 0)
                    ddy = max(abs(v - .5) - (.5 - rad), 0)
                    if ddx * ddx + ddy * ddy > rad * rad:
                        continue
                    cnt += 1
                    if any(dist(u, v, a, b) <= hw for a, b in segs):
                        R += 255; G += 255; B += 255
                    else:
                        k = (u + v) / 2
                        R += c1[0] + (c2[0] - c1[0]) * k
                        G += c1[1] + (c2[1] - c1[1]) * k
                        B += c1[2] + (c2[2] - c1[2]) * k
            if cnt:
                row += bytes((int(R / cnt), int(G / cnt), int(B / cnt), int(255 * cnt / (ss * ss))))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return rows


def _png(n, rows):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_ico(path):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [_png(s, _render_icon(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    entries = b""
    for s, d in zip(sizes, imgs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off)
        off += len(d)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(imgs))


def ensure_icon():
    if os.path.exists(ICO_FILE):
        return ICO_FILE
    os.makedirs(DATA_DIR, exist_ok=True)
    bundled = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "W.ico")
    try:
        if os.path.exists(bundled):
            with open(bundled, "rb") as s, open(ICO_FILE, "wb") as d:
                d.write(s.read())
        else:
            write_ico(ICO_FILE)
    except Exception:
        write_ico(ICO_FILE)
    return ICO_FILE


# ───────────────────────── ярлык на рабочем столе ─────────────────────────
def create_desktop_shortcut():
    ico = ensure_icon()
    if getattr(sys, "frozen", False):
        target, args = sys.executable, ""
        wd = os.path.dirname(sys.executable)
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        target = pyw if os.path.exists(pyw) else sys.executable
        args = '"%s"' % os.path.abspath(__file__)
        wd = os.path.dirname(os.path.abspath(__file__))
    q = lambda s: s.replace("'", "''")
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\\Wexxyy Tweaker.lnk');"
        f"$s.TargetPath='{q(target)}';$s.Arguments='{q(args)}';$s.WorkingDirectory='{q(wd)}';"
        f"$s.IconLocation='{q(ico)},0';$s.Description='Wexxyy Tweaker';$s.Save()"
    )
    code, out = run(script, ps=True)
    # обновить кэш иконок, чтобы W появилась сразу
    run("ie4uinit.exe -show")
    return code == 0


# ───────────────────────── запуск команд / реестр ─────────────────────────
def run(cmd, ps=False):
    try:
        if ps:
            enc = base64.b64encode(cmd.encode("utf-16-le")).decode()
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
            r = subprocess.run(args, capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        else:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def _hive(n):
    return winreg.HKEY_LOCAL_MACHINE if n == "HKLM" else winreg.HKEY_CURRENT_USER


def _rtype(n):
    return {"DWORD": winreg.REG_DWORD, "SZ": winreg.REG_SZ, "BINARY": winreg.REG_BINARY}[n]


def reg_read(hive, path, name):
    try:
        with winreg.OpenKey(_hive(hive), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            v, t = winreg.QueryValueEx(k, name)
            return True, t, v
    except OSError:
        return False, None, None


def reg_write(hive, path, name, rtype, value):
    if rtype == "BINARY" and isinstance(value, str):
        value = bytes.fromhex(value)
    with winreg.CreateKeyEx(_hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, name, 0, _rtype(rtype), value)


def reg_delete(hive, path, name):
    try:
        with winreg.OpenKey(_hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def reg_matches(a):
    _, hive, path, name, rtype, val, _d = a
    ex, t, cur = reg_read(hive, path, name)
    if not ex:
        return False
    if rtype == "DWORD":
        return int(cur) & 0xFFFFFFFF == int(val) & 0xFFFFFFFF
    if rtype == "BINARY":
        return bytes(cur) == bytes.fromhex(val)
    return str(cur) == str(val)


# ───────────────────────── описание твиков ─────────────────────────
@dataclass
class Tweak:
    id: str
    section: str
    name: str
    desc: str
    actions: list = field(default_factory=list)
    risky: bool = False
    oneshot: bool = False


TWEAKS = []


def T(sec, id, name, desc, *actions, risky=False, oneshot=False):
    TWEAKS.append(Tweak(id, sec, name, desc, list(actions), risky, oneshot))


def reg(hive, path, name, value, rtype="DWORD", default=None):
    return ("reg", hive, path, name, rtype, value, default)


def sh(apply, revert=None):
    return ("sh", apply, revert)


def ps(apply, revert=None):
    return ("ps", apply, revert)


def svc(name, default="demand"):
    return ("sh", f"sc config {name} start= disabled & sc stop {name}", f"sc config {name} start= {default}")


def pc(sub, setting, on, off):
    base = "powercfg -setacvalueindex SCHEME_CURRENT %s %s %s & powercfg -setactive SCHEME_CURRENT"
    return sh(base % (sub, setting, on), base % (sub, setting, off))


def bcd(key, val):
    return sh(f"bcdedit /set {key} {val}", f"bcdedit /deletevalue {key}")


def task(path, enable_default=True):
    return sh(f'schtasks /Change /TN "{path}" /Disable', f'schtasks /Change /TN "{path}" /Enable')


EXP = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
ADV = EXP + r"\Advanced"
CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
SYSP = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
GAMES = SYSP + r"\Tasks\Games"
MM = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
TCP = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
POL = r"SOFTWARE\Policies\Microsoft\Windows"
DC = POL + r"\DataCollection"
GCS = r"System\GameConfigStore"
GFX = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
DSK = r"Control Panel\Desktop"
PERS = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# ── Процессор ──
S = "Процессор"
T(S, "cpu_throttle", "Отключить Power Throttling", "Windows не душит фоновые процессы и игры ради экономии энергии.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1))
T(S, "cpu_prio", "Приоритет активного окна (0x26)", "Больше процессорного времени для программы, с которой вы работаете.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation", 38, default=2))
T(S, "cpu_sysresp", "SystemResponsiveness = 0", "Отдаёт максимум CPU приоритетным мультимедиа-задачам и играм.", reg("HKLM", SYSP, "SystemResponsiveness", 0, default=20))
T(S, "cpu_nthrottle", "Отключить NetworkThrottlingIndex", "Убирает ограничение сетевых пакетов при мультимедиа-нагрузке.", reg("HKLM", SYSP, "NetworkThrottlingIndex", 4294967295, default=10))
T(S, "cpu_gpuprio", "GPU Priority для игр = 8", "Повышает приоритет графики для задач категории Games.", reg("HKLM", GAMES, "GPU Priority", 8))
T(S, "cpu_gameprio", "CPU Priority для игр = 6", "Повышает приоритет процессора для задач категории Games.", reg("HKLM", GAMES, "Priority", 6, default=2))
T(S, "cpu_gamesched", "Scheduling Category = High", "Планировщик считает игры высокоприоритетными.", reg("HKLM", GAMES, "Scheduling Category", "High", "SZ", default="Medium"))
T(S, "cpu_gamesfio", "SFIO Priority = High", "Высокий приоритет дисковых операций игр.", reg("HKLM", GAMES, "SFIO Priority", "High", "SZ", default="Normal"))
T(S, "cpu_minstate", "Минимальное состояние CPU 100%", "Процессор не снижает частоту в простое (больше нагрев и расход).", pc("SUB_PROCESSOR", "PROCTHROTTLEMIN", 100, 5))
T(S, "cpu_boost", "Агрессивный Turbo Boost", "Режим повышения частоты — агрессивный.", pc("SUB_PROCESSOR", "PERFBOOSTMODE", 2, 1))
T(S, "cpu_parking", "Отключить парковку ядер", "Все ядра всегда активны — меньше задержек в играх.", pc("SUB_PROCESSOR", "CPMINCORES", 100, 10))
T(S, "cpu_idle", "Запретить простой ядер (Idle Disable)", "Максимальная отзывчивость, но сильный нагрев и расход энергии.", pc("SUB_PROCESSOR", "IDLEDISABLE", 1, 0), risky=True)
T(S, "cpu_dyntick", "Отключить Dynamic Tick", "Стабильнее таймер, меньше микрофризов. Нужна перезагрузка.", bcd("disabledynamictick", "yes"))
T(S, "cpu_platclock", "Отключить Platform Clock (HPET)", "Принудительно не использовать HPET как таймер. Нужна перезагрузка.", bcd("useplatformclock", "false"))
T(S, "cpu_tsc", "TSC Sync Policy = Enhanced", "Улучшенная синхронизация таймера ядер. Нужна перезагрузка.", bcd("tscsyncpolicy", "enhanced"))
T(S, "cpu_timerres", "Глобальное разрешение таймера", "Разрешает программам запрашивать точный таймер (Windows 11).", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "GlobalTimerResolutionRequests", 1))
T(S, "cpu_fth", "Отключить Fault Tolerant Heap", "Windows не включает защитные «костыли» для вылетающих программ.", reg("HKLM", r"SOFTWARE\Microsoft\FTH", "Enabled", 0, default=1))
T(S, "cpu_spectre", "Отключить защиту Spectre/Meltdown", "Прирост на старых CPU, но снижает безопасность. Нужна перезагрузка.", reg("HKLM", MM, "FeatureSettingsOverride", 3), reg("HKLM", MM, "FeatureSettingsOverrideMask", 3), risky=True)
T(S, "cpu_vbs", "Отключить целостность памяти (VBS/HVCI)", "Заметный прирост FPS на некоторых системах, но ниже защита. Перезагрузка.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity", "Enabled", 0), risky=True)

# ── Оперативная память ──
S = "Оперативная память"
T(S, "ram_pagingexec", "Ядро и драйверы держать в ОЗУ", "DisablePagingExecutive — не выгружать код ядра в файл подкачки.", reg("HKLM", MM, "DisablePagingExecutive", 1, default=0))
T(S, "ram_prefetch", "Отключить Prefetcher", "Не нужен на SSD, экономит ОЗУ и записи.", reg("HKLM", MM + r"\PrefetchParameters", "EnablePrefetcher", 0, default=3))
T(S, "ram_superfetch", "Отключить Superfetch (реестр)", "Прекращает предзагрузку программ в память.", reg("HKLM", MM + r"\PrefetchParameters", "EnableSuperfetch", 0, default=3))
T(S, "ram_memcomp", "Отключить сжатие памяти", "Меньше нагрузка на CPU, если ОЗУ достаточно (от 16 ГБ).", ps("Disable-MMAgent -MemoryCompression", "Enable-MMAgent -MemoryCompression"))
T(S, "ram_pagecomb", "Отключить Page Combining", "Не объединять одинаковые страницы памяти.", ps("Disable-MMAgent -PageCombining", "Enable-MMAgent -PageCombining"))
T(S, "ram_clearpf", "Очищать файл подкачки при выключении", "Безопаснее, но выключение дольше.", reg("HKLM", MM, "ClearPageFileAtShutdown", 1, default=0))
T(S, "ram_dump", "Отключить дампы памяти при сбое", "Не создавать огромные файлы дампов.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\CrashControl", "CrashDumpEnabled", 0, default=7))
T(S, "ram_autoreboot", "Не перезагружаться при BSOD", "Синий экран остаётся на экране — можно прочитать код ошибки.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\CrashControl", "AutoReboot", 0, default=1))
T(S, "ram_wer", "Отключить отчёты об ошибках", "Windows не собирает и не отправляет отчёты о сбоях.", reg("HKLM", r"SOFTWARE\Microsoft\Windows\Windows Error Reporting", "Disabled", 1, default=0))
T(S, "ram_timeouts", "Быстрое закрытие программ", "Сокращает ожидание зависших приложений до 1–2 секунд.", reg("HKCU", DSK, "WaitToKillAppTimeout", "2000", "SZ", "5000"), reg("HKCU", DSK, "HungAppTimeout", "1000", "SZ", "5000"), reg("HKCU", DSK, "AutoEndTasks", "1", "SZ", "0"), reg("HKCU", DSK, "LowLevelHooksTimeout", 1000))
T(S, "ram_svctimeout", "Быстрая остановка служб", "WaitToKillServiceTimeout = 2 сек — быстрее выключение.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control", "WaitToKillServiceTimeout", "2000", "SZ", "5000"))
T(S, "ram_bgapps", "Запретить фоновые приложения", "Приложения Microsoft Store не работают в фоне.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1), reg("HKLM", POL + r"\AppPrivacy", "LetAppsRunInBackground", 2))
T(S, "ram_hibernate", "Отключить гибернацию", "Удаляет hiberfil.sys — освобождает место, равное объёму ОЗУ.", sh("powercfg -h off", "powercfg -h on"))
T(S, "ram_faststart", "Отключить быстрый запуск", "Чистая загрузка системы при каждом включении, меньше багов.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled", 0, default=1))

# ── Диск и SSD ──
S = "Диск и SSD"
T(S, "disk_lastaccess", "Не писать время последнего доступа", "Меньше записей на диск при чтении файлов.", sh("fsutil behavior set disablelastaccess 1", "fsutil behavior set disablelastaccess 2"))
T(S, "disk_8dot3", "Отключить короткие имена 8.3", "Ускоряет работу с папками с большим числом файлов.", sh("fsutil behavior set disable8dot3 1", "fsutil behavior set disable8dot3 2"))
T(S, "disk_memusage", "Больше кэша пула NTFS", "fsutil memoryusage 2 — больше памяти под метаданные NTFS.", sh("fsutil behavior set memoryusage 2", "fsutil behavior set memoryusage 1"))
T(S, "disk_defrag", "Отключить плановую дефрагментацию", "SSD не нуждается в дефрагментации (TRIM продолжит работать).", task(r"\Microsoft\Windows\Defrag\ScheduledDefrag"))
T(S, "disk_trim", "Включить TRIM", "Гарантирует работу TRIM для SSD.", sh("fsutil behavior set disabledeletenotify 0", "fsutil behavior set disabledeletenotify 0"))
T(S, "disk_reserved", "Отключить зарезервированное хранилище", "Освобождает ~7 ГБ, которые Windows резервирует под обновления.", sh("DISM /Online /Set-ReservedStorageState /State:Disabled", "DISM /Online /Set-ReservedStorageState /State:Enabled"))
T(S, "disk_storagesense", "Отключить Контроль памяти", "Windows не удаляет файлы автоматически.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy", "01", 0, default=1))
T(S, "disk_thumbs", "Отключить кэш миниатюр", "Не создавать файлы Thumbs.db / кэш превью.", reg("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableThumbnailCache", 1), reg("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableThumbsDBOnNetworkFolders", 1))
T(S, "disk_maint", "Отключить автообслуживание Windows", "Windows не запускает обслуживание в простое ПК.", reg("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\Maintenance", "MaintenanceDisabled", 1, default=0))
T(S, "disk_do", "Отключить раздачу обновлений другим ПК", "Delivery Optimization больше не использует ваш диск и канал.", reg("HKLM", POL + r"\DeliveryOptimization", "DODownloadMode", 0))
T(S, "disk_lowspace", "Не показывать «мало места на диске»", "Убирает уведомления о нехватке места.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer", "NoLowDiskSpaceChecks", 1))

# ── Сеть и пинг ──
S = "Сеть и пинг"
T(S, "net_nagle", "Отключить алгоритм Нейгла", "TcpAckFrequency=1 и TCPNoDelay=1 — ниже пинг в играх.",
  ps(r"Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces' | ForEach-Object { Set-ItemProperty -Path $_.PSPath -Name TcpAckFrequency -Value 1 -Type DWord; Set-ItemProperty -Path $_.PSPath -Name TCPNoDelay -Value 1 -Type DWord }",
     r"Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces' | ForEach-Object { Remove-ItemProperty -Path $_.PSPath -Name TcpAckFrequency,TCPNoDelay -ErrorAction SilentlyContinue }"))
T(S, "net_ecn", "Включить ECN", "Явное уведомление о перегрузке сети.", sh("netsh int tcp set global ecncapability=enabled", "netsh int tcp set global ecncapability=disabled"))
T(S, "net_rsc", "Отключить RSC", "Receive Segment Coalescing увеличивает задержку — выключаем.", sh("netsh int tcp set global rsc=disabled", "netsh int tcp set global rsc=enabled"))
T(S, "net_rto", "Начальный RTO = 2000 мс", "Быстрее повторная отправка потерянных пакетов.", sh("netsh int tcp set global initialRto=2000", "netsh int tcp set global initialRto=3000"))
T(S, "net_heur", "Отключить эвристику TCP", "Windows не меняет параметры автонастройки сама.", sh("netsh int tcp set heuristics disabled", "netsh int tcp set heuristics enabled"))
T(S, "net_dca", "Включить DCA", "Direct Cache Access — быстрее обработка пакетов (если поддерживает сетевая карта).", sh("netsh int tcp set global dca=enabled", "netsh int tcp set global dca=disabled"))
T(S, "net_ports", "Больше портов и быстрый TIME_WAIT", "MaxUserPort=65534, TcpTimedWaitDelay=30.", reg("HKLM", TCP, "MaxUserPort", 65534, default=None), reg("HKLM", TCP, "TcpTimedWaitDelay", 30))
T(S, "net_ttl", "DefaultTTL = 64", "Стандартное значение TTL для большинства сетей.", reg("HKLM", TCP, "DefaultTTL", 64))
T(S, "net_icmp", "Отключить ICMP-редиректы", "Защита от подмены маршрутов.", reg("HKLM", TCP, "EnableICMPRedirect", 0, default=1))
T(S, "net_ipv6", "Отключить IPv6", "Если провайдер не использует IPv6 — меньше сетевой «возни». Нужна перезагрузка.", reg("HKLM", r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters", "DisabledComponents", 255, default=0), risky=True)
T(S, "net_teredo", "Отключить Teredo", "Туннель IPv6 поверх IPv4 — обычно не нужен.", sh("netsh interface teredo set state disabled", "netsh interface teredo set state default"))
T(S, "net_6to4", "Отключить 6to4", "Устаревший туннельный протокол.", sh("netsh interface 6to4 set state disabled", "netsh interface 6to4 set state default"))
T(S, "net_isatap", "Отключить ISATAP", "Устаревший туннельный протокол.", sh("netsh interface isatap set state disabled", "netsh interface isatap set state default"))
T(S, "net_dnscache", "Увеличить кэш DNS", "MaxCacheTtl=86400, MaxNegativeCacheTtl=0.", reg("HKLM", r"SYSTEM\CurrentControlSet\Services\Dnscache\Parameters", "MaxCacheTtl", 86400), reg("HKLM", r"SYSTEM\CurrentControlSet\Services\Dnscache\Parameters", "MaxNegativeCacheTtl", 0, default=900))
T(S, "net_llmnr", "Отключить LLMNR", "Меньше лишнего широковещательного трафика, выше безопасность.", reg("HKLM", POL.replace("Windows", "Windows NT") + r"\DNSClient", "EnableMulticast", 0))
T(S, "net_netbios", "Отключить NetBIOS поверх TCP/IP", "Устаревший протокол, не нужен дома.",
  ps("Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object IPEnabled | Invoke-CimMethod -MethodName SetTcpipNetbios -Arguments @{TcpipNetbiosOptions=2}",
     "Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object IPEnabled | Invoke-CimMethod -MethodName SetTcpipNetbios -Arguments @{TcpipNetbiosOptions=0}"))
T(S, "net_qos", "Снять резерв канала QoS", "Windows не резервирует часть интернета под служебные нужды.", reg("HKLM", POL + r"\Psched", "NonBestEffortLimit", 0))
T(S, "net_nicpower", "Отключить энергосбережение адаптеров", "Сетевые карты не «засыпают» — стабильнее пинг.",
  ps("Get-NetAdapter | ForEach-Object { Disable-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue }",
     "Get-NetAdapter | ForEach-Object { Enable-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue }"))
T(S, "net_lso", "Отключить Large Send Offload", "Меньше задержка в играх на некоторых сетевых картах.",
  ps("Get-NetAdapter | ForEach-Object { Disable-NetAdapterLso -Name $_.Name -ErrorAction SilentlyContinue }",
     "Get-NetAdapter | ForEach-Object { Enable-NetAdapterLso -Name $_.Name -ErrorAction SilentlyContinue }"))
T(S, "net_smb1", "Отключить SMBv1", "Старый уязвимый протокол — отключить безопасно.",
  ps("Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart", "Enable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart"))
T(S, "net_ncsi", "Отключить проверку интернета NCSI", "Windows не обращается к серверам Microsoft для проверки связи.", reg("HKLM", POL + r"\NetworkConnectivityStatusIndicator", "NoActiveProbe", 1))
T(S, "net_dns_cf", "DNS Cloudflare 1.1.1.1", "Быстрый и приватный DNS для активных адаптеров.",
  ps("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ('1.1.1.1','1.0.0.1') }",
     "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }"))
T(S, "net_dns_g", "DNS Google 8.8.8.8", "Альтернатива Cloudflare (не включайте оба сразу).",
  ps("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ('8.8.8.8','8.8.4.4') }",
     "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }"))
T(S, "net_irp", "Увеличить IRPStackSize", "Больше стек запросов для сетевых операций.", reg("HKLM", r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "IRPStackSize", 32))

# ── Графика и игры ──
S = "Графика и игры"
T(S, "gfx_dvr", "Отключить Xbox Game DVR", "Выключает фоновую запись игр — больше FPS.", reg("HKCU", GCS, "GameDVR_Enabled", 0, default=1), reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0, default=1), reg("HKLM", POL + r"\GameDVR", "AllowGameDVR", 0))
T(S, "gfx_fso", "Отключить оптимизацию полного экрана", "Классический эксклюзивный полноэкранный режим в играх.", reg("HKCU", GCS, "GameDVR_FSEBehaviorMode", 2), reg("HKCU", GCS, "GameDVR_HonorUserFSEBehaviorMode", 1), reg("HKCU", GCS, "GameDVR_FSEBehavior", 2), reg("HKCU", GCS, "GameDVR_DXGIHonorFSEWindowsCompatible", 1))
T(S, "gfx_gamemode", "Включить игровой режим", "Windows приостанавливает фоновые задачи во время игры.", reg("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1), reg("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1))
T(S, "gfx_gamebar", "Отключить подсказки Game Bar", "Убирает всплывающие панели Xbox Game Bar.", reg("HKCU", r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", 0, default=1), reg("HKCU", r"Software\Microsoft\GameBar", "ShowStartupPanel", 0, default=1))
T(S, "gfx_hags", "Аппаратное планирование GPU (HAGS)", "Включает HAGS на поддерживаемых видеокартах. Нужна перезагрузка.", reg("HKLM", GFX, "HwSchMode", 2, default=1))
T(S, "gfx_windowed", "Оптимизация игр в окне", "Улучшает FPS и VRR в оконном режиме DirectX 10/11.", reg("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", "DirectXUserGlobalSettings", "SwapEffectUpgradeEnable=1;VRROptimizeEnable=1;", "SZ"))
T(S, "gfx_mpo", "Отключить Multiplane Overlay", "Лечит мерцание и чёрные экраны на некоторых видеокартах.", reg("HKLM", r"SOFTWARE\Microsoft\Windows\Dwm", "OverlayTestMode", 5))
T(S, "gfx_tdr", "Увеличить TdrDelay", "Видеодрайвер получает 10 сек на ответ — меньше сбросов драйвера.", reg("HKLM", GFX, "TdrDelay", 10), reg("HKLM", GFX, "TdrDdiDelay", 10))
T(S, "gfx_transp", "Отключить прозрачность", "Меньше нагрузка на GPU в интерфейсе.", reg("HKCU", PERS, "EnableTransparency", 0, default=1))
T(S, "gfx_anim", "Отключить анимации окон", "Окна сворачиваются и открываются мгновенно.", reg("HKCU", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", "SZ", "1"), reg("HKCU", EXP + r"\VisualEffects", "VisualFXSetting", 2, default=0))
T(S, "gfx_mask", "Режим «Наилучшее быстродействие»", "UserPreferencesMask: отключает тени, плавность и прочие эффекты.", reg("HKCU", DSK, "UserPreferencesMask", "9012038010000000", "BINARY"))

# ── Fortnite: пути (нужны для раздела Potato Graphics) ──
POTATO_SEC = "Potato Graphics"
FN_PROC = "FortniteClient-Win64-Shipping.exe"
FN_INI = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FortniteGame", "Saved", "Config", "WindowsClient", "GameUserSettings.ini")


def find_fortnite_exe():
    """Ищет Fortnite через манифест Epic Launcher, иначе берёт стандартный путь."""
    paths = []
    try:
        dat = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat")
        with open(dat, "r", encoding="utf-8") as f:
            for it in json.load(f).get("InstallationList", []):
                if str(it.get("AppName", "")).lower() == "fortnite":
                    paths.append(os.path.join(it["InstallLocation"], "FortniteGame", "Binaries", "Win64", FN_PROC))
    except Exception:
        pass
    paths.append(os.path.join(r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64", FN_PROC))
    for p in paths:
        if os.path.exists(p):
            return os.path.normpath(p)
    return os.path.normpath(paths[-1])


FN_EXE = find_fortnite_exe()
MOUSE = r"Control Panel\Mouse"
HID_Q = r"SYSTEM\CurrentControlSet\Services"

# ── Мышь ──
S = "Мышь"
T(S, "gfx_mouse", "Отключить ускорение мыши", "Точность указателя — 1:1, лучше для шутеров.", reg("HKCU", MOUSE, "MouseSpeed", "0", "SZ", "1"), reg("HKCU", MOUSE, "MouseThreshold1", "0", "SZ", "6"), reg("HKCU", MOUSE, "MouseThreshold2", "0", "SZ", "10"))
T(S, "ms_sens", "Скорость указателя 6/11 (без множителя)", "Нейтральная чувствительность Windows: курсор двигается ровно так, как задаёт DPI мыши.", reg("HKCU", MOUSE, "MouseSensitivity", "10", "SZ", "10"))
T(S, "ms_queue", "Меньшая очередь ввода мыши (mouclass)", "Размер буфера драйвера мыши 16 вместо 100 — события обрабатываются быстрее. Нужна перезагрузка. Для мышей 4000–8000 Гц не рекомендуется.", reg("HKLM", HID_Q + r"\mouclass\Parameters", "MouseDataQueueSize", 16, default=100))
T(S, "ms_usbpower", "Не отключать USB-устройства для экономии", "Мышь и клавиатура по USB не «засыпают» и не просыпаются с задержкой.", ps(
    r'''Get-WmiObject MSPower_DeviceEnable -Namespace root\wmi -ErrorAction SilentlyContinue | ForEach-Object { if ($_.InstanceName -like "USB*") { $_.Enable = $false; $_.psbase.Put() | Out-Null } }''',
    r'''Get-WmiObject MSPower_DeviceEnable -Namespace root\wmi -ErrorAction SilentlyContinue | ForEach-Object { if ($_.InstanceName -like "USB*") { $_.Enable = $true; $_.psbase.Put() | Out-Null } }'''))
T(S, "ms_affinity", "Авто-привязка USB-контроллера мыши к ядру", "Прерывания USB-контроллеров (к ним подключена мышь) обрабатываются на выделенном ядре №2, а не на ядре 0, куда падает вся системная нагрузка. Ядро выбирается автоматически. Нужна перезагрузка.", ps(
    r'''$n = [Environment]::ProcessorCount
if ($n -ge 4) { $core = 2 } elseif ($n -ge 2) { $core = 1 } else { $core = 0 }
$mask = [BitConverter]::GetBytes([int64](1 -shl $core))
Get-PnpDevice -Class USB -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'PCI\*' } | ForEach-Object {
  $p = 'HKLM:\SYSTEM\CurrentControlSet\Enum\' + $_.InstanceId + '\Device Parameters\Interrupt Management\Affinity Policy'
  New-Item -Path $p -Force | Out-Null
  New-ItemProperty -Path $p -Name DevicePolicy -Value 4 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $p -Name AssignmentSetOverride -Value $mask -PropertyType Binary -Force | Out-Null
}''',
    r'''Get-PnpDevice -Class USB -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'PCI\*' } | ForEach-Object {
  $p = 'HKLM:\SYSTEM\CurrentControlSet\Enum\' + $_.InstanceId + '\Device Parameters\Interrupt Management\Affinity Policy'
  Remove-ItemProperty -Path $p -Name DevicePolicy, AssignmentSetOverride -ErrorAction SilentlyContinue
}'''))

# ── Клавиатура ──
S = "Клавиатура"
T(S, "gfx_kbd", "Быстрый отклик клавиатуры", "Минимальная задержка и максимальная скорость повтора.", reg("HKCU", r"Control Panel\Keyboard", "KeyboardDelay", "0", "SZ", "1"), reg("HKCU", r"Control Panel\Keyboard", "KeyboardSpeed", "31", "SZ", "31"))
T(S, "gfx_sticky", "Отключить залипание клавиш", "Не мешает Shift×5 в играх.", reg("HKCU", r"Control Panel\Accessibility\StickyKeys", "Flags", "506", "SZ", "510"), reg("HKCU", r"Control Panel\Accessibility\Keyboard Response", "Flags", "122", "SZ", "126"), reg("HKCU", r"Control Panel\Accessibility\ToggleKeys", "Flags", "58", "SZ", "62"))
T(S, "kb_queue", "Меньшая очередь ввода клавиатуры (kbdclass)", "Размер буфера драйвера клавиатуры 16 вместо 100 — нажатия обрабатываются быстрее. Нужна перезагрузка.", reg("HKLM", HID_Q + r"\kbdclass\Parameters", "KeyboardDataQueueSize", 16, default=100))
T(S, "kb_langkey", "Отключить смену раскладки Alt+Shift / Ctrl+Shift", "Раскладка не слетает посреди игры. Менять можно через Win+Пробел. Применится после перезахода в систему.", reg("HKCU", r"Keyboard Layout\Toggle", "Hotkey", "3", "SZ", "1"), reg("HKCU", r"Keyboard Layout\Toggle", "Language Hotkey", "3", "SZ", "1"), reg("HKCU", r"Keyboard Layout\Toggle", "Layout Hotkey", "3", "SZ", "2"))
T(S, "kb_winkeys", "Отключить сочетания с клавишей Win", "Win+… больше не сворачивает игру случайным нажатием. Применится после перезапуска Проводника.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer", "NoWinKeys", 1))
T(S, "kb_caps", "Отключить Caps Lock", "Клавиша перестаёт срабатывать (через Scancode Map). Заменит уже имеющуюся карту клавиш — откат вернёт прежнюю. Нужна перезагрузка.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\Keyboard Layout", "Scancode Map", "00000000" "00000000" "02000000" "00003a00" "00000000", "BINARY"))
T(S, "kb_typing", "Отключить сбор данных рукописного и печатного ввода", "Windows не анализирует то, что вы печатаете, для «персонализации».", reg("HKCU", r"Software\Microsoft\InputPersonalization", "RestrictImplicitTextCollection", 1, default=0), reg("HKCU", r"Software\Microsoft\InputPersonalization", "RestrictImplicitInkCollection", 1, default=0), reg("HKCU", r"Software\Microsoft\Input\TIPC", "Enabled", 0, default=1))
T(S, "kb_tablet", "Отключить службу сенсорной клавиатуры", "TabletInputService — экранная клавиатура и рукописный ввод, на ПК без сенсора не нужны.", svc("TabletInputService", "demand"))

# ── Potato Graphics (Fortnite + NVIDIA) ──
S = POTATO_SEC
T(S, "pt_gpupref", "Fortnite → высокая производительность GPU", "Windows запускает игру на дискретной видеокарте NVIDIA (важно для ноутбуков и систем с iGPU).", reg("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", FN_EXE, "GpuPreference=2;", "SZ"))
T(S, "pt_fso", "Fortnite: отключить оптимизацию полного экрана", "Флаг совместимости для Fortnite — меньше задержка ввода и просадок кадров.", reg("HKCU", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers", FN_EXE, "~ DISABLEDXMAXIMIZEDWINDOWEDMODE", "SZ"))
T(S, "pt_prio", "Fortnite: высокий приоритет процесса", "Процесс игры всегда стартует с приоритетом High (через PerfOptions).", reg("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options" + "\\" + FN_PROC + r"\PerfOptions", "CpuPriorityClass", 3))
T(S, "pt_nvtele", "NVIDIA: отключить телеметрию и фоновые задачи", "Останавливает NvTelemetryContainer и задачи NvTm* / NvProfileUpdater* — меньше фоновой нагрузки.", svc("NvTelemetryContainer", "auto"), ps(
    r'''Get-ScheduledTask -TaskName 'NvTm*','NvProfileUpdater*' -ErrorAction SilentlyContinue | Disable-ScheduledTask | Out-Null''',
    r'''Get-ScheduledTask -TaskName 'NvTm*','NvProfileUpdater*' -ErrorAction SilentlyContinue | Enable-ScheduledTask | Out-Null'''))
T(S, "pt_nvcache", "NVIDIA: очистить шейдерный кэш (разово)", "Удаляет DXCache и GLCache. Лечит микрофризы от битого кэша; первые матчи после очистки могут подлагивать, пока кэш наберётся заново.", sh(r'cmd /c "rd /s /q %LOCALAPPDATA%\NVIDIA\DXCache & rd /s /q %LOCALAPPDATA%\NVIDIA\GLCache"'), oneshot=True)
T(S, "pt_logs", "Fortnite: очистить логи и веб-кэш (разово)", "Удаляет Logs, Crashes и webcache* из папки FortniteGame\\Saved.", ps(
    r'''$b = "$env:LOCALAPPDATA\FortniteGame\Saved"
Remove-Item "$b\Logs\*", "$b\Crashes\*" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $b -Directory -Filter 'webcache*' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue'''), oneshot=True)

# ── Приватность ──
S = "Приватность"
T(S, "prv_telemetry", "Минимум телеметрии", "AllowTelemetry = 0 (только безопасность).", reg("HKLM", DC, "AllowTelemetry", 0), reg("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection", "AllowTelemetry", 0))
T(S, "prv_feedback", "Отключить запросы отзывов", "Windows не спрашивает ваше мнение.", reg("HKLM", DC, "DoNotShowFeedbackNotifications", 1))
T(S, "prv_adid", "Отключить рекламный ID", "Приложения не получают ваш рекламный идентификатор.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0, default=1), reg("HKLM", POL + r"\AdvertisingInfo", "DisabledByGroupPolicy", 1))
T(S, "prv_tailored", "Отключить персонализированные советы", "Microsoft не использует диагностику для подбора рекламы.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Privacy", "TailoredExperiencesWithDiagnosticDataEnabled", 0, default=1))
T(S, "prv_cortana", "Отключить Cortana", "Выключает голосового помощника.", reg("HKLM", POL + r"\Windows Search", "AllowCortana", 0))
T(S, "prv_bing", "Отключить Bing в поиске Пуск", "Поиск ищет только на ПК.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", 0, default=1), reg("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1))
T(S, "prv_websearch", "Отключить веб-поиск Windows", "Запросы не уходят в интернет.", reg("HKLM", POL + r"\Windows Search", "ConnectedSearchUseWeb", 0), reg("HKLM", POL + r"\Windows Search", "DisableWebSearch", 1))
T(S, "prv_activity", "Отключить журнал действий", "Timeline / история действий не собирается.", reg("HKLM", POL + r"\System", "EnableActivityFeed", 0), reg("HKLM", POL + r"\System", "PublishUserActivities", 0), reg("HKLM", POL + r"\System", "UploadUserActivities", 0))
T(S, "prv_location", "Отключить геолокацию", "Система не определяет ваше местоположение.", reg("HKLM", POL + r"\LocationAndSensors", "DisableLocation", 1))
T(S, "prv_cdm_start", "Убрать рекомендации в Пуске", "Нет предложенных приложений и рекламы в меню.", reg("HKCU", CDM, "SubscribedContent-338388Enabled", 0, default=1), reg("HKCU", CDM, "SystemPaneSuggestionsEnabled", 0, default=1))
T(S, "prv_cdm_lock", "Убрать рекламу на экране блокировки", "Отключает Spotlight-подсказки и ротацию.", reg("HKCU", CDM, "RotatingLockScreenEnabled", 0, default=1), reg("HKCU", CDM, "RotatingLockScreenOverlayEnabled", 0, default=1), reg("HKCU", CDM, "SubscribedContent-338387Enabled", 0, default=1))
T(S, "prv_cdm_auto", "Запретить автоустановку приложений", "Отключает «рекомендуемые» игры и приложения Microsoft.", reg("HKCU", CDM, "SilentInstalledAppsEnabled", 0, default=1), reg("HKCU", CDM, "PreInstalledAppsEnabled", 0, default=1), reg("HKCU", CDM, "OemPreInstalledAppsEnabled", 0, default=1), reg("HKCU", CDM, "ContentDeliveryAllowed", 0, default=1))
T(S, "prv_cdm_tips", "Отключить подсказки и советы", "Нет уведомлений «Как получить максимум от Windows».", reg("HKCU", CDM, "SubscribedContent-338389Enabled", 0, default=1), reg("HKCU", CDM, "SoftLandingEnabled", 0, default=1))
T(S, "prv_trackprogs", "Не отслеживать запуск программ", "Пуск не запоминает часто используемые приложения.", reg("HKCU", ADV, "Start_TrackProgs", 0, default=1))
T(S, "prv_ceip", "Отключить программу улучшения (CEIP)", "Не отправлять данные об использовании.", reg("HKLM", r"SOFTWARE\Policies\Microsoft\SQMClient\Windows", "CEIPEnable", 0))
T(S, "prv_appcompat", "Отключить телеметрию совместимости", "Выключает инвентаризацию приложений и PCA.", reg("HKLM", POL + r"\AppCompat", "AITEnable", 0), reg("HKLM", POL + r"\AppCompat", "DisableInventory", 1), reg("HKLM", POL + r"\AppCompat", "DisablePCA", 1))
T(S, "prv_ink", "Отключить сбор рукописного и текстового ввода", "Не собирать образцы набора текста.", reg("HKCU", r"Software\Microsoft\InputPersonalization", "RestrictImplicitInkCollection", 1, default=0), reg("HKCU", r"Software\Microsoft\InputPersonalization", "RestrictImplicitTextCollection", 1, default=0))
T(S, "prv_speech", "Отключить онлайн-распознавание речи", "Голос не отправляется в облако.", reg("HKCU", r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy", "HasAccepted", 0, default=1))
T(S, "prv_tasks", "Отключить задачи телеметрии", "Выключает плановые задания Compatibility Appraiser, CEIP и т.п.",
  task(r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser"),
  task(r"\Microsoft\Windows\Application Experience\ProgramDataUpdater"),
  task(r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator"),
  task(r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip"))
T(S, "prv_remote", "Отключить удалённого помощника", "Закрывает вход через Remote Assistance.", reg("HKLM", r"SYSTEM\CurrentControlSet\Control\Remote Assistance", "fAllowToGetHelp", 0, default=1))
T(S, "prv_copilot", "Отключить Copilot", "Убирает Windows Copilot.", reg("HKCU", r"Software\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot", 1))
T(S, "prv_recall", "Отключить Recall / анализ ИИ", "Запрещает локальную запись и анализ экрана.", reg("HKCU", r"Software\Policies\Microsoft\Windows\WindowsAI", "DisableAIDataAnalysis", 1))
T(S, "prv_onedrive", "Отключить OneDrive", "Синхронизация файлов не запускается.", reg("HKLM", POL + r"\OneDrive", "DisableFileSyncNGSC", 1))
T(S, "prv_edge", "Отключить фоновую работу Edge", "Edge не стартует заранее и не живёт в фоне.", reg("HKLM", r"SOFTWARE\Policies\Microsoft\Edge", "StartupBoostEnabled", 0), reg("HKLM", r"SOFTWARE\Policies\Microsoft\Edge", "BackgroundModeEnabled", 0))
T(S, "prv_clip", "Отключить историю буфера обмена", "Win+V не сохраняет и не синхронизирует буфер.", reg("HKCU", r"Software\Microsoft\Clipboard", "EnableClipboardHistory", 0, default=1), reg("HKLM", POL + r"\System", "AllowCrossDeviceClipboard", 0))
T(S, "prv_insider", "Отключить программу Insider", "Блокирует предварительные сборки.", reg("HKLM", DC, "AllowBuildPreview", 0))
T(S, "prv_feeds", "Отключить «Новости и интересы»", "Нет ленты новостей на панели задач.", reg("HKLM", POL + r"\Windows Feeds", "EnableFeeds", 0))
T(S, "prv_sync", "Отключить синхронизацию настроек", "Настройки не отправляются в облако Microsoft.", reg("HKLM", POL + r"\SettingSync", "DisableSettingSync", 2), reg("HKLM", POL + r"\SettingSync", "DisableSettingSyncUserOverride", 1))

# ── Интерфейс ──
S = "Интерфейс"
T(S, "ui_ext", "Показывать расширения файлов", "Видно .exe, .txt, .png и т.д.", reg("HKCU", ADV, "HideFileExt", 0, default=1))
T(S, "ui_hidden", "Показывать скрытые файлы", "Скрытые папки и файлы видны в Проводнике.", reg("HKCU", ADV, "Hidden", 1, default=2))
T(S, "ui_super", "Показывать системные файлы", "Включает защищённые системные файлы (осторожно).", reg("HKCU", ADV, "ShowSuperHidden", 1, default=0), risky=True)
T(S, "ui_launchto", "Проводник открывается на «Этот компьютер»", "Вместо «Быстрого доступа».", reg("HKCU", ADV, "LaunchTo", 1, default=2))
T(S, "ui_classicmenu", "Классическое контекстное меню (Win11)", "Возвращает полное меню ПКМ. Перезапустите Проводник.",
  sh(r'reg add "HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32" /f /ve',
     r'reg delete "HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}" /f'))
T(S, "ui_tbleft", "Панель задач слева (Win11)", "Значки как в Windows 10.", reg("HKCU", ADV, "TaskbarAl", 0, default=1))
T(S, "ui_taskview", "Скрыть кнопку «Представление задач»", "Освобождает место на панели задач.", reg("HKCU", ADV, "ShowTaskViewButton", 0, default=1))
T(S, "ui_chat", "Скрыть кнопку Чат / Teams", "Убирает иконку чата.", reg("HKCU", ADV, "TaskbarMn", 0, default=1))
T(S, "ui_widgets", "Скрыть кнопку Виджетов", "Убирает иконку виджетов.", reg("HKCU", ADV, "TaskbarDa", 0, default=1))
T(S, "ui_search", "Скрыть поиск на панели задач", "Поиск доступен по Win+S.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "SearchboxTaskbarMode", 0, default=2))
T(S, "ui_seconds", "Секунды в часах", "Показывать секунды в системных часах.", reg("HKCU", ADV, "ShowSecondsInSystemClock", 1, default=0))
T(S, "ui_shake", "Отключить Aero Shake", "Окна не сворачиваются при встряхивании.", reg("HKCU", ADV, "DisallowShaking", 1, default=0))
T(S, "ui_menudelay", "Мгновенное открытие меню", "MenuShowDelay = 0.", reg("HKCU", DSK, "MenuShowDelay", "0", "SZ", "400"))
T(S, "ui_shortcut", "Без «- Ярлык» в названии ярлыков", "Новые ярлыки создаются с чистым именем.", reg("HKCU", EXP, "link", "00000000", "BINARY"))
T(S, "ui_recent", "Отключить недавние файлы в быстром доступе", "Проводник не запоминает последние файлы и папки.", reg("HKCU", EXP, "ShowRecent", 0, default=1), reg("HKCU", EXP, "ShowFrequent", 0, default=1))
T(S, "ui_lockscreen", "Отключить экран блокировки", "Сразу показывать ввод пароля.", reg("HKLM", POL + r"\Personalization", "NoLockScreen", 1))
T(S, "ui_dark", "Тёмная тема", "Тёмная тема для приложений и системы.", reg("HKCU", PERS, "AppsUseLightTheme", 0, default=1), reg("HKCU", PERS, "SystemUsesLightTheme", 0, default=1))
T(S, "ui_startdelay", "Убрать задержку автозагрузки", "Программы автозагрузки стартуют без паузы.", reg("HKCU", EXP + r"\Serialize", "StartupDelayInMSec", 0))
T(S, "ui_startsound", "Отключить звук запуска Windows", "Без мелодии при включении.", reg("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "DisableStartupSound", 1, default=0))
T(S, "ui_autoplay", "Отключить автозапуск носителей", "Флешки и диски не запускаются сами.", reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers", "DisableAutoplay", 1, default=0))

# ── Службы ──
S = "Службы"
T(S, "svc_sysmain", "SysMain (Superfetch)", "Предзагрузка программ в память — на SSD не нужна.", svc("SysMain", "auto"))
T(S, "svc_wsearch", "Windows Search (индексация)", "Отключает индексацию — поиск файлов станет медленнее.", svc("WSearch", "delayed-auto"))
T(S, "svc_diagtrack", "DiagTrack (телеметрия)", "Служба сбора диагностических данных.", svc("DiagTrack", "auto"))
T(S, "svc_dmwap", "dmwappushservice", "Служба маршрутизации push-сообщений WAP.", svc("dmwappushservice", "demand"))
T(S, "svc_fax", "Факс", "Служба факса — почти никому не нужна.", svc("Fax"))
T(S, "svc_maps", "Диспетчер карт", "Загрузка офлайн-карт.", svc("MapsBroker", "delayed-auto"))
T(S, "svc_retail", "Демонстрация розничной торговли", "Служба режима демо-витрины.", svc("RetailDemo"))
T(S, "svc_geo", "Геолокация (lfsvc)", "Определение местоположения.", svc("lfsvc"))
T(S, "svc_xbox", "Службы Xbox (4 шт.)", "Могут понадобиться для Xbox / Game Pass.", svc("XblAuthManager"), svc("XblGameSave"), svc("XboxNetApiSvc"), svc("XboxGipSvc"), risky=True)
T(S, "svc_phone", "Служба телефона", "Связь с телефоном (PhoneSvc).", svc("PhoneSvc"))
T(S, "svc_wmp", "Общий доступ к проигрывателю WMP", "Служба WMPNetworkSvc.", svc("WMPNetworkSvc"))
T(S, "svc_wer", "Служба отчётов об ошибках", "WerSvc.", svc("WerSvc"))
T(S, "svc_spooler", "Диспетчер печати", "Отключайте, только если не пользуетесь принтерами.", svc("Spooler", "auto"), risky=True)
T(S, "svc_bt", "Bluetooth", "Отключайте, только если не пользуетесь Bluetooth.", svc("bthserv"), risky=True)
T(S, "svc_bio", "Биометрия (Windows Hello)", "Отпечаток пальца и лицо перестанут работать.", svc("WbioSrvc"), risky=True)
T(S, "svc_hotspot", "Мобильный хот-спот", "Служба общего доступа к интернету (icssvc).", svc("icssvc"))
T(S, "svc_wisvc", "Служба Insider", "wisvc.", svc("wisvc"))
T(S, "svc_trk", "Отслеживание связей (TrkWks)", "Устаревшая служба отслеживания ярлыков.", svc("TrkWks", "auto"))
T(S, "svc_parental", "Родительский контроль", "WpcMonSvc.", svc("WpcMonSvc"))
T(S, "svc_semgr", "Диспетчер платежей и NFC", "SEMgrSvc.", svc("SEMgrSvc"))

# ── Питание ──
S = "Питание"
T(S, "pwr_ultimate", "План «Максимальная производительность»", "Создаёт и включает скрытую схему питания.", sh("powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 & powercfg -setactive e9a42b02-d5df-448d-aa00-03f14749eb61", "powercfg -setactive 381b4222-f694-41f0-9685-ff5bb260df2e"))
T(S, "pwr_usb", "Отключить USB selective suspend", "USB-устройства не «засыпают».", pc("2a737441-1930-4402-8d77-b2bebba308a3", "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0, 1))
T(S, "pwr_pcie", "Отключить энергосбережение PCIe", "Link State Power Management — выкл. Лучше для видеокарты.", pc("SUB_PCIEXPRESS", "ASPM", 0, 1))
T(S, "pwr_disk", "Не выключать диск", "Диски всегда работают (от сети).", sh("powercfg -change -disk-timeout-ac 0", "powercfg -change -disk-timeout-ac 20"))
T(S, "pwr_sleep", "Не уходить в сон от сети", "Спящий режим выключен при питании от розетки.", sh("powercfg -change -standby-timeout-ac 0", "powercfg -change -standby-timeout-ac 30"))
T(S, "pwr_hybrid", "Отключить гибридный сон", "Не сохранять образ памяти при уходе в сон.", pc("SUB_SLEEP", "HYBRIDSLEEP", 0, 1))
T(S, "pwr_wake", "Запретить таймеры пробуждения", "Компьютер не просыпается сам по расписанию.", pc("SUB_SLEEP", "RTCWAKE", 0, 1))

# ── Очистка (разовые действия) ──
S = "Очистка"
T(S, "cln_temp", "Очистить временные файлы", "Удаляет содержимое папок TEMP пользователя и Windows.", sh(r'cmd /c "del /q /f /s %TEMP%\* & del /q /f /s C:\Windows\Temp\*"'), oneshot=True)
T(S, "cln_prefetch", "Очистить папку Prefetch", "Удаляет файлы предзагрузки.", sh(r'cmd /c "del /q /f /s C:\Windows\Prefetch\*"'), oneshot=True)
T(S, "cln_wu", "Очистить кэш Windows Update", "Удаляет скачанные, но не установленные обновления.", sh(r'net stop wuauserv & net stop bits & rd /s /q C:\Windows\SoftwareDistribution\Download & net start bits & net start wuauserv'), oneshot=True)
T(S, "cln_dns", "Сбросить кэш DNS", "ipconfig /flushdns.", sh("ipconfig /flushdns"), oneshot=True)
T(S, "cln_winsock", "Сбросить Winsock и IP", "Лечит проблемы с сетью. Нужна перезагрузка.", sh("netsh winsock reset & netsh int ip reset"), oneshot=True)
T(S, "cln_recycle", "Очистить корзину", "Удаляет всё из корзины на всех дисках.", ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue"), oneshot=True)
T(S, "cln_dism", "Очистка компонентов Windows (DISM)", "Удаляет старые версии обновлений. Может идти несколько минут.", sh("Dism.exe /online /Cleanup-Image /StartComponentCleanup"), oneshot=True)
T(S, "cln_explorer", "Перезапустить Проводник", "Применяет изменения интерфейса без перезагрузки.", sh("taskkill /f /im explorer.exe & start explorer.exe"), oneshot=True)

SECTIONS = []
for _t in TWEAKS:
    if _t.section not in SECTIONS:
        SECTIONS.append(_t.section)


# ───────────────────────── применение / откат / состояние ─────────────────────────
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            d.setdefault("applied", {})
            return d
    except Exception:
        return {"applied": {}}


def save_state(st):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def is_active(t, st):
    if t.oneshot:
        return False
    regs = [a for a in t.actions if a[0] == "reg"]
    if regs and len(regs) == len(t.actions):
        return all(reg_matches(a) for a in regs)
    return t.id in st["applied"]


def _enc(v):
    return ["b", bytes(v).hex()] if isinstance(v, (bytes, bytearray)) else ["v", v]


def _dec(e):
    return bytes.fromhex(e[1]) if e[0] == "b" else e[1]


def apply_tweak(t, st, log):
    backups = [] if t.id in st["applied"] else None
    fresh = []
    for a in t.actions:
        if a[0] == "reg":
            _, hive, path, name, rtype, val, _d = a
            ex, typ, old = reg_read(hive, path, name)
            fresh.append([hive, path, name, ex, typ, _enc(old) if ex else None])
            reg_write(hive, path, name, rtype, val)
        else:
            code, out = run(a[1], ps=(a[0] == "ps"))
            if code != 0:
                log(f"   ! код {code}: {out.strip()[:160]}")
    if backups is None:
        st["applied"][t.id] = {"backup": fresh}
    save_state(st)


def revert_tweak(t, st, log):
    bk = {tuple(b[:3]): b for b in st["applied"].get(t.id, {}).get("backup", [])}
    for a in t.actions:
        if a[0] == "reg":
            _, hive, path, name, rtype, val, default = a
            b = bk.get((hive, path, name))
            if b:
                if b[3]:
                    reg_write(hive, path, name, {winreg.REG_DWORD: "DWORD", winreg.REG_BINARY: "BINARY"}.get(b[4], "SZ"), _dec(b[5]))
                else:
                    reg_delete(hive, path, name)
            elif default is not None:
                reg_write(hive, path, name, rtype, default)
            else:
                reg_delete(hive, path, name)
        elif a[2]:
            code, out = run(a[2], ps=(a[0] == "ps"))
            if code != 0:
                log(f"   ! код {code}: {out.strip()[:160]}")
    st["applied"].pop(t.id, None)
    save_state(st)


def create_restore_point():
    run('Enable-ComputerRestore -Drive "$env:SystemDrive\\"', ps=True)
    return run('Checkpoint-Computer -Description "Wexxyy Tweaker" -RestorePointType MODIFY_SETTINGS', ps=True)


# ───────────────────────── сеть: пинг и DNS ─────────────────────────
REGIONS = {
    "eu-central-1": ("FRANKFURT", "DE"), "eu-west-1": ("IRELAND", "IE"), "eu-west-2": ("LONDON", "GB"),
    "eu-west-3": ("PARIS", "FR"), "eu-north-1": ("STOCKHOLM", "SE"),
    "us-east-1": ("VIRGINIA", "US"), "us-east-2": ("OHIO", "US"),
    "us-west-1": ("CALIFORNIA", "US"), "us-west-2": ("OREGON", "US"),
    "ap-northeast-1": ("TOKYO", "JP"), "ap-northeast-2": ("SEOUL", "KR"), "ap-southeast-1": ("SINGAPORE", "SG"),
    "ap-southeast-2": ("SYDNEY", "AU"), "ap-south-1": ("MUMBAI", "IN"), "sa-east-1": ("SAO PAULO", "BR"),
}
_EU = ["eu-central-1", "eu-west-2", "eu-west-3", "eu-north-1", "eu-west-1"]
GAMES = {
    "Fortnite — EU": _EU,
    "Fortnite — NA East": ["us-east-1", "us-east-2"],
    "Fortnite — NA West": ["us-west-1", "us-west-2"],
    "Fortnite — Asia": ["ap-northeast-1", "ap-northeast-2", "ap-southeast-1"],
    "CS2 — EU": _EU, "Valorant — EU": _EU, "Dota 2 — EU": _EU,
    "Apex Legends — EU": _EU, "Warzone — EU": _EU, "League of Legends — EUW": _EU,
    "Любая игра — весь мир": list(REGIONS),
}
DNS_SERVERS = [("Cloudflare", "1.1.1.1", "1.0.0.1"), ("Google", "8.8.8.8", "8.8.4.4"),
               ("Quad9", "9.9.9.9", "149.112.112.112"), ("OpenDNS", "208.67.222.222", "208.67.220.220"),
               ("AdGuard", "94.140.14.14", "94.140.15.15")]


def tcp_ping(host, port=443, n=5, timeout=1.5, stop=None):
    import socket, time
    ip = None
    cands = [host]
    if host.startswith("ec2.") and host.endswith(".amazonaws.com"):
        reg = host[4:-len(".amazonaws.com")]
        cands += ["dynamodb.%s.amazonaws.com" % reg, "sts.%s.amazonaws.com" % reg]
    for h in cands:
        try:
            ip = socket.gethostbyname(h)
            break
        except OSError:
            continue
    if ip is None:
        return {"avg": None, "min": None, "jitter": None, "loss": 100}
    ts, fails = [], 0
    for _ in range(n):
        if stop is not None and stop.is_set():
            break
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            ts.append((time.perf_counter() - t0) * 1000)
            s.close()
        except OSError:
            fails += 1
        time.sleep(0.05)
    sent = len(ts) + fails
    if not ts:
        return {"avg": None, "min": None, "jitter": None, "loss": 100}
    jitter = sum(abs(a - b) for a, b in zip(ts, ts[1:])) / (len(ts) - 1) if len(ts) > 1 else 0.0
    return {"avg": sum(ts) / len(ts), "min": min(ts), "jitter": jitter, "loss": round(100 * fails / sent)}


def dns_packet(name, rid):
    q = struct.pack(">HHHHHH", rid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return q + struct.pack(">HH", 1, 1)


def dns_ping(server, timeout=1.5):
    import socket, time, random, statistics
    vals = []
    for name in ("example.com", "wikipedia.org", "microsoft.com"):
        rid = random.randint(0, 65535)
        pkt = dns_packet(name, rid)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            t0 = time.perf_counter()
            s.sendto(pkt, (server, 53))
            data = s.recv(512)
            dt = (time.perf_counter() - t0) * 1000
            if data[:2] == pkt[:2]:
                vals.append(dt)
        except OSError:
            pass
        finally:
            s.close()
    return statistics.median(vals) if vals else None


def set_dns(primary, secondary):
    return run(f"Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object {{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ('{primary}','{secondary}') }}", ps=True)


def reset_dns():
    return run("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }", ps=True)


# ───────────────────────── Fortnite: пинг в игре + запрет Discord ─────────────────────────
DISCORD_RULE = "Wexxyy Block Discord"
DISCORD_PROCS = ["Discord.exe", "DiscordPTB.exe", "DiscordCanary.exe", "DiscordDevelopment.exe"]


def find_discord_exes():
    """Ищет установленный Discord (Stable/PTB/Canary) в %LOCALAPPDATA%."""
    base = os.environ.get("LOCALAPPDATA", "")
    out = []
    for folder, exe in (("Discord", "Discord.exe"), ("DiscordPTB", "DiscordPTB.exe"), ("DiscordCanary", "DiscordCanary.exe")):
        root = os.path.join(base, folder)
        if not os.path.isdir(root):
            continue
        upd = os.path.join(root, "Update.exe")
        if os.path.exists(upd):
            out.append(upd)
        try:
            for d in sorted(os.listdir(root)):
                p = os.path.join(root, d, exe)
                if d.lower().startswith("app-") and os.path.exists(p):
                    out.append(p)
        except OSError:
            pass
    return out


def discord_blocked():
    code, _out = run('netsh advfirewall firewall show rule name="%s"' % DISCORD_RULE)
    return code == 0


def discord_unblock():
    run('netsh advfirewall firewall delete rule name="%s"' % DISCORD_RULE)
    return True, "Discord разблокирован: правила брандмауэра удалены."


def discord_block():
    """Блокирует Discord в брандмауэре Windows (вход и выход) и закрывает его. -> (ok, текст)"""
    exes = find_discord_exes()
    if not exes:
        return False, "Discord не найден в %LOCALAPPDATA%\\Discord — блокировать нечего."
    run('netsh advfirewall firewall delete rule name="%s"' % DISCORD_RULE)
    for p in exes:
        for d in ("out", "in"):
            run('netsh advfirewall firewall add rule name="%s" dir=%s action=block program="%s" enable=yes profile=any'
                % (DISCORD_RULE, d, p))
    for n in DISCORD_PROCS:
        run("taskkill /F /IM %s" % n)
    return True, "Discord заблокирован в брандмауэре (%d файл.) и закрыт. После обновления Discord нажмите заново." % len(exes)


# ───────────────────────── конфиги игроков Fortnite ─────────────────────────
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")
PLAYERS_DIR = os.path.join(DATA_DIR, "players")
# Значения из открытых источников (prosettings.net и обзоры, 2026). У разных сайтов они расходятся
# и со временем меняются — поэтому каждую запись можно править в players.json. None = нет данных.
PLAYERS_DEFAULT = [
    {"id": "peterbot", "name": "Peterbot", "team": "Falcons Esports", "dpi": 800, "hz": 1000, "x": 6.4, "y": 6.4,
     "target": 45, "scope": 45, "build": 100, "edit": 100, "res": "1920x1080 (native)",
     "note": "Данные prosettings.net."},
    {"id": "bugha", "name": "Bugha", "team": "", "dpi": 800, "hz": None, "x": 6.4, "y": 6.4,
     "target": 45, "scope": 45, "build": None, "edit": None, "res": None,
     "note": "Источники расходятся: 6–6.4% по X/Y."},
    {"id": "mongraal", "name": "Mongraal", "team": "", "dpi": 800, "hz": None, "x": 6.7, "y": 6.7,
     "target": 50, "scope": 50, "build": None, "edit": None, "res": "1920x1080",
     "note": "Источники расходятся: 6.5–6.7% по X/Y."},
    {"id": "clix", "name": "Clix", "team": "", "dpi": 800, "hz": None, "x": 8.7, "y": 6.3,
     "target": 60, "scope": 35, "build": None, "edit": None, "res": None,
     "note": "Источники расходятся (есть вариант 7%/7%)."},
    {"id": "mrsavage", "name": "MrSavage", "team": "", "dpi": 800, "hz": None, "x": 9.1, "y": 9.1,
     "target": 49, "scope": 49, "build": None, "edit": None, "res": None, "note": ""},
    {"id": "ptd_mert1k", "name": "PTD MERT1K", "team": "", "dpi": 800, "hz": 1000, "x": 6.5, "y": 8.9,
     "target": 30, "scope": 65, "build": 110, "edit": 120, "res": None,
     "note": "СЛУЧАЙНЫЙ конфиг — это не реальные настройки игрока."},
]


def load_players():
    try:
        with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = [p for p in data if isinstance(p, dict) and p.get("id") and p.get("name")]
        if out:
            return out
    except Exception:
        pass
    return [dict(p) for p in PLAYERS_DEFAULT]


def ensure_players_file():
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    seen_file = os.path.join(DATA_DIR, "players_seen.json")
    try:
        if not os.path.exists(PLAYERS_FILE):
            with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
                json.dump(PLAYERS_DEFAULT, f, ensure_ascii=False, indent=2)
            seen = [p["id"] for p in PLAYERS_DEFAULT]
        else:
            try:
                with open(seen_file, "r", encoding="utf-8") as f:
                    seen = json.load(f)
            except Exception:
                seen = [p["id"] for p in PLAYERS_DEFAULT if p["id"] != "ptd_mert1k"]
            with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
                cur = json.load(f)
            have = {p.get("id") for p in cur if isinstance(p, dict)}
            new = [p for p in PLAYERS_DEFAULT if p["id"] not in seen and p["id"] not in have]
            if new:
                with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
                    json.dump(cur + new, f, ensure_ascii=False, indent=2)
            seen = [p["id"] for p in PLAYERS_DEFAULT]
        with open(seen_file, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except Exception:
        pass


def player_text(p):
    def f(v, suf=""):
        return "—" if v in (None, "") else ("%g%s" % (v, suf) if isinstance(v, (int, float)) else "%s%s" % (v, suf))
    edpi = ""
    try:
        edpi = "%.1f" % (float(p["dpi"]) * float(p["x"]) / 100.0)
    except Exception:
        edpi = "—"
    return ("%s\nDPI: %s | Polling: %s Hz | eDPI: %s\nSens X/Y: %s%% / %s%%\nTargeting: %s%% | Scope: %s%%\n"
            "Building: %s%% | Editing: %s%%\nРазрешение: %s") % (
        p["name"], f(p.get("dpi")), f(p.get("hz")), edpi, f(p.get("x")), f(p.get("y")), f(p.get("target")),
        f(p.get("scope")), f(p.get("build")), f(p.get("edit")), f(p.get("res")))


FN_CFG_BAK = FN_INI + ".wexxyy-cfg.bak"
_SENS_SKIP = ("gamepad", "controller", "touch", "mobile", "gyro", "pad", "stick")


def _sens_class(k):
    """По имени ключа ini определяет, какая это чувствительность мыши (x/y/target/scope/build/edit)."""
    import re
    if "target" in k or k.endswith("ads"):
        return "target"
    if "scope" in k:
        return "scope"
    if "build" in k:
        return "build"
    if "edit" in k:
        return "edit"
    m = re.search(r"sensitivity_?([xy])$", k)
    return m.group(1) if m else None


def fn_cfg_plan(p):
    """Сравнивает конфиг игрока с ключами, которые УЖЕ есть в вашем GameUserSettings.ini. -> (ok, план, текст)"""
    if not os.path.exists(FN_INI):
        return False, [], "GameUserSettings.ini не найден — запустите Fortnite хотя бы один раз."
    with open(FN_INI, "rb") as f:
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    want = {"x": p.get("x"), "y": p.get("y"), "target": p.get("target"), "scope": p.get("scope"),
            "build": p.get("build"), "edit": p.get("edit")}
    head, inside, plan = "[" + FN_SEC.lower() + "]", False, []
    for ln in lines:
        t = ln.strip()
        if t.startswith("["):
            inside = t.lower() == head
            continue
        if not inside or "=" not in t:
            continue
        key, _, old = t.partition("=")
        k = key.strip().lower()
        if "sensitivity" not in k or any(b in k for b in _SENS_SKIP):
            continue
        v = want.get(_sens_class(k))
        if v is None:
            continue
        try:
            o = float(old)
            v = float(v)
        except ValueError:
            continue
        new = v / 100.0 if o <= 1.0 else v      # формат (доля или проценты) берём из текущего значения
        plan.append((key.strip(), old.strip(), "%.6f" % new))
    if not plan:
        return False, [], ("В вашем GameUserSettings.ini нет ключей чувствительности мыши. Откройте Fortnite, один раз измените "
                           "чувствительность в настройках, выйдите из игры и повторите.")
    return True, plan, ""


def fn_cfg_apply(plan):
    import stat
    if fortnite_running():
        return False, "Закройте Fortnite: при выходе он перезапишет файл настроек."
    os.chmod(FN_INI, stat.S_IREAD | stat.S_IWRITE)
    with open(FN_INI, "rb") as f:
        data = f.read()
    with open(FN_CFG_BAK, "wb") as f:
        f.write(data)
    text = data.decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for key, _old, new in plan:
        ini_set(lines, FN_SEC, key, new)
    with open(FN_INI, "wb") as f:
        f.write((nl.join(lines) + nl).encode("utf-8"))
    return True, "Конфиг записан в Fortnite (%d парам.). Бэкап: GameUserSettings.ini.wexxyy-cfg.bak" % len(plan)


def fn_cfg_restore():
    import shutil
    import stat
    if fortnite_running():
        return False, "Закройте Fortnite перед откатом."
    if not os.path.exists(FN_CFG_BAK):
        return False, "Бэкап конфига не найден — он ещё не применялся."
    if os.path.exists(FN_INI):
        os.chmod(FN_INI, stat.S_IREAD | stat.S_IWRITE)
    shutil.copyfile(FN_CFG_BAK, FN_INI)
    return True, "Настройки Fortnite возвращены к состоянию до применения конфига."


# ───────────────────────── NVIDIA: профиль драйвера для Fortnite (через NVIDIA Profile Inspector) ─────────────────────────
NV_NIP = os.path.join(DATA_DIR, "Fortnite_Potato.nip")
NV_RELEASES = "https://github.com/Orbmu2k/nvidiaProfileInspector/releases"
# (название, SettingID, значение). ID и значения сверены с .nip-файлами из открытых репозиториев.
NV_SETTINGS = [
    ("Antialiasing - Mode", 276757595, 1),                    # Override any application setting
    ("Antialiasing - Setting", 282555346, 0),                 # Off
    ("Anisotropic filtering mode", 282245910, 1),             # User-defined
    ("Anisotropic filtering setting", 270426537, 1),          # Off
    ("Texture filtering - Negative LOD bias", 1686376, 0),    # Allow
    ("Texture filtering - Quality", 13510289, 20),            # High performance
    ("Texture filtering - Trilinear optimization", 3066610, 0),   # On
    ("Power management mode", 274197361, 1),                  # Prefer maximum performance
    ("Threaded optimization", 549528094, 1),                  # On
    ("Vertical Sync", 11041231, 138504007),                   # Force off
]
NV_TRSS_ID = 282364549            # Antialiasing - Transparency Supersampling: 0 = Off / Multisampling, 8 = AA_MODE_REPLAY_MODE_ALL
NV_LOD_ID = 7573135               # Texture filtering - LOD Bias (DX): значение = смещение * 8 (0x00 = 0, 0x08 = +1.0, 0x18 = +3.0)
NV_LOD_OPTS = {"0 (по умолчанию)": 0, "+1.0 (0x08, проще текстуры)": 8, "+3.0 (0x18, ещё проще)": 24}
NV_LOWLAT_ID = 390467                                         # Low Latency: 1 = On, 0 = Off (если в игре включён Reflex)


def nv_build_nip(reflex=False, lod=0):
    # LOD Bias работает только при Transparency Supersampling = AA_MODE_REPLAY_MODE_ALL (8); при нулевом LOD — Off / Multisampling (0)
    items = list(NV_SETTINGS) + [("Antialiasing - Transparency Supersampling", NV_TRSS_ID, 8 if lod else 0),
                                 ("Texture filtering - LOD Bias (DX)", NV_LOD_ID, int(lod)),
                                 ("Low Latency Mode", NV_LOWLAT_ID, 0 if reflex else 1)]
    body = "".join(
        "      <ProfileSetting>\n        <SettingNameInfo>%s</SettingNameInfo>\n        <SettingID>%d</SettingID>\n"
        "        <SettingValue>%d</SettingValue>\n        <ValueType>Dword</ValueType>\n      </ProfileSetting>\n" % it
        for it in items)
    return ('<?xml version="1.0" encoding="utf-16"?>\n<ArrayOfProfile>\n  <Profile>\n    <ProfileName>Fortnite</ProfileName>\n'
            '    <Executeables>\n      <string>fortniteclient-win64-shipping.exe</string>\n    </Executeables>\n'
            '    <Settings>\n' + body + '    </Settings>\n  </Profile>\n</ArrayOfProfile>\n')


def nv_write_nip(reflex=False, lod=0):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NV_NIP, "w", encoding="utf-16") as f:
        f.write(nv_build_nip(reflex, lod))
    return NV_NIP


def nv_has_nvidia():
    _c, out = run("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }", ps=True)
    return "nvidia" in out.lower()


def nv_find_inspector(saved=None):
    import glob
    home = os.path.expanduser("~")
    cands = [saved, os.path.join(os.path.dirname(os.path.abspath(__file__)), "nvidiaProfileInspector.exe"),
             os.path.join(DATA_DIR, "nvpi", "nvidiaProfileInspector.exe")]
    for pat in (os.path.join(home, "Downloads", "*", "nvidiaProfileInspector.exe"),
                os.path.join(home, "Downloads", "nvidiaProfileInspector.exe"),
                os.path.join(home, "Desktop", "*", "nvidiaProfileInspector.exe"),
                r"C:\Program Files*\*nvidiaProfileInspector*\nvidiaProfileInspector.exe"):
        cands += glob.glob(pat)
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def nv_download():
    """Скачивает последний релиз NVIDIA Profile Inspector с GitHub (Orbmu2k) в папку программы. -> (путь к exe | None, текст)"""
    import io
    import urllib.request
    import zipfile
    ua = {"User-Agent": "WexxyyTweaker"}
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://api.github.com/repos/Orbmu2k/nvidiaProfileInspector/releases/latest", headers=ua), timeout=20) as r:
            rel = json.load(r)
        asset = next((a for a in rel.get("assets", []) if str(a.get("name", "")).lower().endswith(".zip")), None)
        if not asset:
            return None, "В последнем релизе нет zip-архива — скачайте Inspector вручную."
        with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers=ua), timeout=120) as r:
            raw = r.read()
        dest = os.path.join(DATA_DIR, "nvpi")
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            z.extractall(dest)
        for root, _d, files in os.walk(dest):
            for f in files:
                if f.lower() == "nvidiaprofileinspector.exe":
                    return os.path.join(root, f), "Inspector %s скачан в %s" % (rel.get("tag_name", ""), dest)
        return None, "В архиве не найден nvidiaProfileInspector.exe."
    except Exception as e:
        return None, "Не удалось скачать Inspector (%s). Используйте кнопку «Открыть страницу загрузки»." % e


def nv_backup(exe):
    """Просит Inspector выгрузить изменённые профили (-exportCustomized) и копирует файл в папку программы."""
    import glob
    import shutil
    import time
    d = os.path.dirname(exe)
    before = set(glob.glob(os.path.join(d, "*.nip")))
    t0 = time.time()
    try:
        subprocess.run([exe, "-exportCustomized"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except Exception:
        return None
    new = [p for p in glob.glob(os.path.join(d, "*.nip")) if p not in before or os.path.getmtime(p) >= t0 - 2]
    if not new:
        return None
    dest = os.path.join(DATA_DIR, "nvidia_backup_%s.nip" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        shutil.copyfile(max(new, key=os.path.getmtime), dest)
        return dest
    except Exception:
        return None


def nv_apply(exe, reflex=False, lod=0):
    """Импортирует профиль Fortnite в драйвер NVIDIA. -> (ok, текст)"""
    nip = nv_write_nip(reflex, lod)
    bak = nv_backup(exe)
    try:
        r = subprocess.run([exe, "-silentImport", nip], capture_output=True, text=True, errors="replace", timeout=90,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "Inspector не завершил импорт за 90 с (возможно, ждёт выбора Merge/Replace). Откройте файл вручную: " + nip
    except Exception as e:
        return False, "Не удалось запустить Inspector: %s" % e
    if r.returncode != 0:
        return False, "Inspector вернул код %s. Откройте файл вручную: %s" % (r.returncode, nip)
    return True, ("Профиль NVIDIA для Fortnite применён (%d парам.)." % (len(NV_SETTINGS) + 3)
                  + (" Бэкап: " + bak if bak else " Бэкап профилей создать не удалось (папка Inspector не для записи)."))


# ───────────────────────── настройки внешнего вида ─────────────────────────
UI_FILE = os.path.join(DATA_DIR, "ui.json")
DARK = dict(bg="#0c0d10", side="#101114", card="#14161a", card2="#1a1d22", line="#262930", text="#e9ebf0", muted="#7d8493", ok="#34e0a1", bad="#f2616a", warn="#f5b84a", dark=True)
LIGHT = dict(bg="#eef0f5", side="#ffffff", card="#ffffff", card2="#e9ecf2", line="#d3d8e2", text="#14161c", muted="#6b7280", ok="#12a36b", bad="#d94452", warn="#c78a12", dark=False)
PRESETS = {
    "Неон (зелёный)": (DARK, "#34e0a1"), "Фиолетовый": (DARK, "#8b6cff"), "Красный": (DARK, "#ff5a6a"),
    "Синий": (DARK, "#4aa3ff"), "Оранжевый": (DARK, "#ff9a3d"), "Розовый": (DARK, "#ff6bc1"),
    "Светлая": (LIGHT, "#5b3df5"),
}
ACCENTS = ["#34e0a1", "#8b6cff", "#ff5a6a", "#4aa3ff", "#ff9a3d", "#ff6bc1", "#f5d90a", "#00d1ff"]
DEFAULT_UI = {"preset": "Неон (зелёный)", "accent": None, "scale": 1.0, "radius": 12, "alpha": 1.0, "desc": True, "compact": False}
SCALE_OPTS = {"Маленький": 0.9, "Обычный": 1.0, "Крупный": 1.15}
RAD_OPTS = {"Острые": 4, "Средние": 12, "Круглые": 20}


def load_ui():
    u = dict(DEFAULT_UI)
    try:
        with open(UI_FILE, "r", encoding="utf-8") as f:
            u.update({k: v for k, v in json.load(f).items() if k in DEFAULT_UI})
    except Exception:
        pass
    return u


def save_ui(u):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(UI_FILE, "w", encoding="utf-8") as f:
            json.dump(u, f, ensure_ascii=False)
    except Exception:
        pass


def _rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def valid_hex(s):
    s = (s or "").strip()
    if len(s) == 7 and s[0] == "#":
        try:
            int(s[1:], 16)
            return True
        except ValueError:
            return False
    return False


def build_theme(u):
    base, acc = PRESETS.get(u.get("preset"), PRESETS["Неон (зелёный)"])
    th = dict(base)
    acc = u.get("accent") if valid_hex(u.get("accent")) else acc
    r, g, b = _rgb(acc)
    th["accent"] = acc
    th["accent_h"] = mix(acc, "#000000", 0.18)
    th["on_accent"] = "#0b0d10" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"
    th["nav_text"] = mix(th["muted"], th["text"], 0.55)
    return th


def _import_ctk():
    try:
        import customtkinter as ctk
    except ImportError:
        if getattr(sys, "frozen", False):
            raise
        subprocess.run([sys.executable, "-m", "pip", "install", "customtkinter"], creationflags=NO_WINDOW)
        import customtkinter as ctk
    return ctk


# ───────────────────────── Fortnite: ini-пресет Potato и прицелы ─────────────────────────
FN_SEC = "/Script/FortniteGame.FortGameUserSettings"


def fortnite_running():
    _c, out = run('tasklist /FI "IMAGENAME eq %s" /NH' % FN_PROC)
    return FN_PROC.lower() in out.lower()


def ini_set(lines, section, key, value):
    """Меняет/добавляет key=value в секции ini, не трогая остальной файл."""
    head = "[" + section.lower() + "]"
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() == head:
            start = i
            break
    entry = "%s=%s" % (key, value)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[" + section + "]", entry])
        return
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("["):
            end = j
            break
    prefix, found = key.lower() + "=", False
    for j in range(start + 1, end):
        if lines[j].strip().lower().startswith(prefix):
            lines[j] = entry
            found = True
    if not found:
        pos = end
        while pos > start + 1 and not lines[pos - 1].strip():
            pos -= 1
        lines.insert(pos, entry)


def potato_apply(res, fps, keep_view, perf, lock):
    """Записывает Potato-настройки в GameUserSettings.ini (с бэкапом). -> (ok, текст)"""
    import stat
    p = FN_INI
    if not os.path.exists(p):
        return False, "GameUserSettings.ini не найден — запустите Fortnite хотя бы один раз."
    os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    with open(p, "rb") as f:
        data = f.read()
    bak = p + ".wexxyy.bak"
    if not os.path.exists(bak):
        with open(bak, "wb") as f:
            f.write(data)
    text = data.decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for k, v in [("sg.ResolutionQuality", "%.6f" % res), ("sg.ViewDistanceQuality", "3" if keep_view else "0"),
                 ("sg.AntiAliasingQuality", "0"), ("sg.ShadowQuality", "0"), ("sg.PostProcessQuality", "0"),
                 ("sg.TextureQuality", "0"), ("sg.EffectsQuality", "0"), ("sg.FoliageQuality", "0")]:
        ini_set(lines, "ScalabilityGroups", k, v)
    for k, v in [("bUseVSync", "False"), ("bMotionBlur", "False"), ("bShowGrass", "False"),
                 ("FrameRateLimit", "%.6f" % fps)]:
        ini_set(lines, FN_SEC, k, v)
    if perf:
        ini_set(lines, "D3DRHIPreference", "PreferredRHI", "dx11")
        ini_set(lines, "D3DRHIPreference", "PreferredFeatureLevel", "es31")
    with open(p, "wb") as f:
        f.write((nl.join(lines) + nl).encode("utf-8"))
    if lock:
        os.chmod(p, stat.S_IREAD)
    return True, "Potato-пресет записан" + (" и файл заблокирован (только чтение)." if lock else ".") + " Бэкап: GameUserSettings.ini.wexxyy.bak"


def potato_restore():
    import stat
    import shutil
    bak = FN_INI + ".wexxyy.bak"
    if not os.path.exists(bak):
        return False, "Бэкап не найден — пресет ещё не применялся."
    if os.path.exists(FN_INI):
        os.chmod(FN_INI, stat.S_IREAD | stat.S_IWRITE)
    shutil.copyfile(bak, FN_INI)
    os.chmod(FN_INI, stat.S_IREAD | stat.S_IWRITE)
    return True, "Настройки Fortnite восстановлены из бэкапа."


# ── прицелы ──
CROSS_FILE = os.path.join(DATA_DIR, "crosshair.json")
CROSS_KEY = "#010101"          # цвет, который окно делает прозрачным
CROSS_STYLES = ["Крест", "Т-образный", "Точка", "Круг", "Круг + точка", "Икс", "Квадрат"]
CROSS_COLORS = ["#00ff66", "#ff2d2d", "#ffffff", "#00e5ff", "#ffe600", "#ff00e5", "#ff9a3d"]
CROSS_DEFAULT = {"on": False, "style": "Крест", "color": "#00ff66", "size": 10, "thick": 2, "gap": 4,
                 "alpha": 100, "dot": False, "outline": True, "ox": 0, "oy": 0}
CROSS_PRESETS = {
    "Классика": dict(style="Крест", size=10, thick=2, gap=4, dot=False),
    "Мини-крест": dict(style="Крест", size=5, thick=1, gap=2, dot=True),
    "Т-стиль": dict(style="Т-образный", size=9, thick=2, gap=3, dot=True),
    "Точка": dict(style="Точка", size=8, thick=2, gap=0, dot=False),
    "Круг": dict(style="Круг + точка", size=10, thick=1, gap=0, dot=True),
    "Икс": dict(style="Икс", size=8, thick=2, gap=3, dot=False),
}


def load_cross():
    c = dict(CROSS_DEFAULT)
    try:
        with open(CROSS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in c and type(v) == type(c[k]):
                c[k] = v
    except Exception:
        pass
    if c["style"] not in CROSS_STYLES or not valid_hex(c["color"]):
        c["style"], c["color"] = CROSS_DEFAULT["style"], CROSS_DEFAULT["color"]
    return c


def save_cross(c):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CROSS_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


def draw_crosshair(cv, cx, cy, c):
    """Рисует прицел на tk.Canvas (и для предпросмотра, и для оверлея)."""
    cv.delete("all")
    col = c["color"] if valid_hex(c["color"]) else "#00ff66"
    s, t, g, st = int(c["size"]), int(c["thick"]), int(c["gap"]), c["style"]
    dot_r = max(1.5, t * 0.8)

    def paint(color, w, outline):
        cap = "projecting" if outline else "butt"
        pad = 1 if outline else 0

        def ln(x1, y1, x2, y2):
            cv.create_line(x1, y1, x2, y2, fill=color, width=w, capstyle=cap)
        if st in ("Крест", "Т-образный"):
            ln(cx - g - s, cy, cx - g, cy)
            ln(cx + g, cy, cx + g + s, cy)
            if st == "Крест":
                ln(cx, cy - g - s, cx, cy - g)
            ln(cx, cy + g, cx, cy + g + s)
        elif st == "Икс":
            a, b = g * 0.7, (g + s) * 0.7
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                ln(cx + sx * a, cy + sy * a, cx + sx * b, cy + sy * b)
        elif st == "Точка":
            r = max(1.5, s / 4.0) + pad
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
        elif st in ("Круг", "Круг + точка"):
            cv.create_oval(cx - s, cy - s, cx + s, cy + s, outline=color, width=w)
        elif st == "Квадрат":
            cv.create_rectangle(cx - s, cy - s, cx + s, cy + s, outline=color, width=w)
        if c["dot"] or st == "Круг + точка":
            r = dot_r + pad
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)

    if c["outline"]:
        paint("#000000", t + 2, True)
    paint(col, t, False)


# ───────────────────────── интерфейс ─────────────────────────
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = " ".join('"%s"' % a for a in sys.argv[1:])
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        params = " ".join('"%s"' % a for a in [os.path.abspath(sys.argv[0])] + sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)


STEPS = ["ПРОВЕРКА СЕТИ", "ДОСТУП К УЗЛАМ", "КАЧЕСТВО СВЯЗИ", "ЛУЧШИЙ МАРШРУТ"]


def launch_ui():
    import tkinter as tk
    from tkinter import messagebox
    import functools
    ctk = _import_ctk()

    class App(ctk.CTk):
        def __init__(self):
            ui0 = load_ui()
            th0 = build_theme(ui0)
            ctk.set_appearance_mode("dark" if th0["dark"] else "light")
            super().__init__()
            self.ui, self.th = ui0, th0
            self.title(APP)
            self.geometry("1200x780")
            self.minsize(1040, 700)
            try:
                self.attributes("-alpha", float(self.ui["alpha"]))
            except Exception:
                pass
            ico = ensure_icon()
            self.after(250, lambda: self._set_icon(ico))
            self.protocol("WM_DELETE_WINDOW", self._close)
            self.st = load_state()
            self.vars = {t.id: tk.IntVar(value=1 if is_active(t, self.st) else 0) for t in TWEAKS}
            self.game_var = tk.StringVar(value=list(GAMES)[0])
            self.query = tk.StringVar(value="")
            self.opt_vars = {}
            self.q = queue.Queue()
            self.log_lines = []
            self.last_msg = "Готово. Отметьте твики переключателями и нажмите «Применить»."
            self.busy = False
            self.page = "home"
            self.logbox = None
            self.pill_lbl = None
            self.msg_lbl = None
            self.status = {}
            self.nav = {}
            self.search_job = None
            self.ping = {"running": False, "stop": None, "results": {}, "steps": [0, 0, 0, 0], "best": None,
                         "status": "ГОТОВ К ПРОВЕРКЕ", "dns": {}}
            self.step_lbls, self.step_cells = [], []
            self.cross = load_cross()
            self.cross_win, self.cross_cv, self._cross_loop = None, None, False
            self.fnp = {"win": None, "lbl": None, "run": False, "keep": False, "host": "ec2.eu-central-1.amazonaws.com"}
            self.fn_region_var = tk.StringVar(value="Fortnite — EU")
            self._photos = []
            self._build()
            self.show("home")
            self.after(100, self._poll)
            flag = os.path.join(DATA_DIR, ".shortcut_asked")
            if not os.path.exists(flag):
                os.makedirs(DATA_DIR, exist_ok=True)
                open(flag, "w").close()
                if messagebox.askyesno(APP, "Создать ярлык с иконкой W на рабочем столе?"):
                    self.make_shortcut()

        # ---------- общие помощники ----------
        def _set_icon(self, ico):
            try:
                self.iconbitmap(ico)
            except Exception:
                pass

        def _close(self):
            save_ui(self.ui)
            self.destroy()

        @property
        def R(self):
            return int(self.ui["radius"])

        def F(self, size, bold=False):
            return ctk.CTkFont(family="Segoe UI", size=max(9, int(round(size * float(self.ui["scale"])))), weight="bold" if bold else "normal")

        def card(self, parent):
            th = self.th
            return ctk.CTkFrame(parent, fg_color=th["card"], corner_radius=self.R, border_width=1, border_color=th["line"])

        def label(self, parent, text, size=12, bold=False, color=None, **kw):
            return ctk.CTkLabel(parent, text=text, font=self.F(size, bold), text_color=color or self.th["text"], **kw)

        def btn(self, parent, text, cmd, kind="ghost", width=None, height=38):
            th = self.th
            style = {
                "accent": (th["accent"], th["accent_h"], th["on_accent"]),
                "bad": (th["bad"], mix(th["bad"], "#000000", 0.18), "#ffffff"),
                "ghost": (th["card2"], th["line"], th["text"]),
            }[kind]
            kw = {"width": width} if width else {}
            return ctk.CTkButton(parent, text=text, command=cmd, fg_color=style[0], hover_color=style[1], text_color=style[2],
                                 corner_radius=self.R, height=height, font=self.F(12, True), **kw)

        def alive(self, w):
            try:
                return w is not None and bool(w.winfo_exists())
            except Exception:
                return False

        # ---------- каркас окна ----------
        def _build(self):
            th = self.th
            for w in self.winfo_children():
                w.destroy()
            self.configure(fg_color=th["bg"])
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(0, weight=1)
            side = ctk.CTkFrame(self, fg_color=th["side"], corner_radius=0, width=240)
            side.grid(row=0, column=0, sticky="nsew")
            side.grid_propagate(False)
            logo = ctk.CTkFrame(side, fg_color="transparent")
            logo.pack(fill="x", padx=16, pady=(18, 14))
            ctk.CTkLabel(logo, text="W", width=36, height=36, fg_color=th["accent"], text_color=th["on_accent"],
                         corner_radius=min(self.R, 12), font=self.F(18, True)).pack(side="left")
            self.label(logo, "WEXXYY TWEAKER", 13, True).pack(side="left", padx=10)

            self.nav = {}
            self.nav_btn(side, "look", "ВНЕШНИЙ ВИД", bottom=True)
            self.nav_btn(side, "home", "ГЛАВНАЯ")
            self.nav_btn(side, "ping", "БУСТЕР ПИНГА")
            self.nav_btn(side, "cross", "ПРИЦЕЛЫ")
            self.nav_btn(side, "potato", "POTATO GRAPHICS")
            self.nav_btn(side, "configs", "КОНФИГИ")
            self.label(side, "КАТЕГОРИИ", 11, True, th["muted"], anchor="w").pack(fill="x", padx=22, pady=(14, 4))
            cats = ctk.CTkScrollableFrame(side, fg_color="transparent", scrollbar_button_color=th["line"],
                                          scrollbar_button_hover_color=th["muted"])
            cats.pack(fill="both", expand=True, padx=(0, 2))
            for sec in SECTIONS:
                if sec != POTATO_SEC:
                    self.nav_btn(cats, "sec:" + sec, sec.upper())

            self.content = ctk.CTkFrame(self, fg_color="transparent")
            self.content.grid(row=0, column=1, sticky="nsew", padx=22, pady=(18, 10))
            self.content.grid_columnconfigure(0, weight=1)
            self.content.grid_rowconfigure(1, weight=1)
            # оверлей прицела — дочернее окно, при перестройке интерфейса оно уничтожается
            self.cross_win, self._cross_loop = None, False
            if self.cross.get("on"):
                self.after(80, self.cross_show)
            self.fnp["win"] = None
            if self.st.get("fn_ping"):
                self.after(120, self.fnp_show)

        def nav_btn(self, parent, key, text, bottom=False):
            th = self.th
            b = ctk.CTkButton(parent, text=text, anchor="w", height=34, corner_radius=self.R, fg_color="transparent",
                              hover_color=th["card2"], text_color=th["nav_text"], font=self.F(12, True),
                              command=lambda k=key: self.show(k))
            if bottom:
                b.pack(side="bottom", fill="x", padx=12, pady=(2, 16))
            else:
                b.pack(fill="x", padx=12, pady=1)
            self.nav[key] = b

        def show(self, key):
            th = self.th
            self.page = key
            self.status = {}
            self.logbox = None
            prev = getattr(self, "_prev_key", None)
            self._prev_key = key
            self._nav_anim_id = getattr(self, "_nav_anim_id", 0) + 1
            for k, b in self.nav.items():
                on = k == key
                if on:
                    b.configure(text_color=th["text"])
                    self._nav_fade(b, th["side"], th["card2"], self._nav_anim_id, 0)
                elif k == prev:
                    b.configure(text_color=th["nav_text"])
                    self._nav_fade(b, th["card2"], th["side"], self._nav_anim_id, 0, off=True)
                else:
                    b.configure(fg_color="transparent", text_color=th["nav_text"])
            for w in self.content.winfo_children():
                w.destroy()
            titles = {"home": "ГЛАВНАЯ", "ping": "БУСТЕР ПИНГА", "look": "ВНЕШНИЙ ВИД", "cross": "ПРИЦЕЛЫ", "potato": "POTATO GRAPHICS", "configs": "КОНФИГИ"}
            title = titles.get(key, key.split(":", 1)[-1].upper())
            pill = self.pill_text(key)
            head = ctk.CTkFrame(self.content, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            self.label(head, title, 22, True).pack(side="left")
            pf = ctk.CTkFrame(head, fg_color="transparent", border_width=1, border_color=th["ok"], corner_radius=16)
            pf.pack(side="right")
            self.pill_lbl = self.label(pf, pill, 11, True, th["ok"])
            self.pill_lbl.pack(padx=14, pady=5)
            self.body = ctk.CTkFrame(self.content, fg_color="transparent")
            self.body.grid(row=1, column=0, sticky="nsew", padx=(34 if prev not in (None, key) else 0, 0))
            self.msg_lbl = self.label(self.content, self.last_msg, 11, False, th["muted"], anchor="w")
            self.msg_lbl.grid(row=2, column=0, sticky="ew", pady=(8, 0))
            if key == "home":
                self.page_home()
            elif key == "ping":
                self.page_ping()
            elif key == "look":
                self.page_look()
            elif key == "cross":
                self.page_cross()
            elif key == "potato":
                self.page_potato()
            elif key == "configs":
                self.page_configs()
            else:
                self.page_tweaks(key.split(":", 1)[1])
            if prev not in (None, key):
                self._slide_in(self._nav_anim_id, 34)

        def _nav_fade(self, btn, c1, c2, token, i, off=False):
            """Плавная смена цвета кнопки меню."""
            steps = 8
            if token != self._nav_anim_id or not self.alive(btn):
                return
            t = (i + 1) / steps
            t = 1 - (1 - t) ** 2
            try:
                btn.configure(fg_color=mix(c1, c2, t))
                if i + 1 >= steps and off:
                    btn.configure(fg_color="transparent")
            except Exception:
                return
            if i + 1 < steps:
                self.after(16, lambda: self._nav_fade(btn, c1, c2, token, i + 1, off))

        def _slide_in(self, token, offset, i=0):
            """Контент выезжает слева направо с замедлением в конце."""
            steps = 9
            if token != self._nav_anim_id or not self.alive(self.body):
                return
            t = (i + 1) / steps
            off = int(offset * (1 - t) ** 3)
            try:
                self.body.grid_configure(padx=(off, 0))
            except Exception:
                return
            if i + 1 < steps:
                self.after(14, lambda: self._slide_in(token, offset, i + 1))

        def pill_text(self, key):
            if key == "home":
                return f"ВСЕГО ТВИКОВ: {len(TWEAKS)}"
            if key == "ping":
                return self.ping["status"]
            if key == "look":
                return "ТЕМА: " + self.ui["preset"].upper()
            if key == "cross":
                return "ПРИЦЕЛ: " + ("ВКЛ" if self.cross.get("on") else "ВЫКЛ")
            if key == "configs":
                return "ИГРОКОВ: %d" % len(load_players())
            sec = POTATO_SEC if key == "potato" else key.split(":", 1)[1]
            items = [t for t in TWEAKS if t.section == sec]
            if sec == "Очистка":
                return f"ДЕЙСТВИЙ: {len(items)}"
            n = sum(1 for t in items if is_active(t, self.st))
            return f"ВКЛЮЧЕНО: {n}/{len(items)}"

        def scroll(self, parent):
            th = self.th
            sf = ctk.CTkScrollableFrame(parent, fg_color="transparent", scrollbar_button_color=th["line"],
                                        scrollbar_button_hover_color=th["muted"])
            sf.pack(fill="both", expand=True)
            return sf

        # ---------- журнал и очередь ----------
        def log(self, msg):
            self.q.put(msg)

        def refresh_page(self):
            self.show(self.page)

        def _poll(self):
            try:
                while True:
                    m = self.q.get_nowait()
                    if callable(m):
                        try:
                            m()
                        except Exception:
                            pass
                    elif m == "__DONE__":
                        self.busy = False
                        self.refresh_page()
                    else:
                        self.log_lines.append(m)
                        self.log_lines = self.log_lines[-300:]
                        self.last_msg = m
                        if self.alive(self.msg_lbl):
                            self.msg_lbl.configure(text=m)
                        if self.alive(self.logbox):
                            self.logbox.configure(state="normal")
                            self.logbox.insert("end", m + "\n")
                            self.logbox.see("end")
                            self.logbox.configure(state="disabled")
            except queue.Empty:
                pass
            self.after(100, self._poll)

        # ---------- главная ----------
        def page_home(self):
            th = self.th
            sf = self.scroll(self.body)
            active = sum(1 for t in TWEAKS if is_active(t, self.st))
            row = ctk.CTkFrame(sf, fg_color="transparent")
            row.pack(fill="x", pady=(0, 12))
            for i, (lab, val, col) in enumerate([("ВСЕГО ТВИКОВ", len(TWEAKS), th["text"]), ("АКТИВНО СЕЙЧАС", active, th["ok"]), ("РАЗДЕЛОВ", len(SECTIONS), th["text"])]):
                row.grid_columnconfigure(i, weight=1, uniform="h")
                c = self.card(row)
                c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 6 if i == 2 else 0))
                self.label(c, lab, 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 0))
                self.label(c, str(val), 30, True, col, anchor="w").pack(fill="x", padx=18, pady=(0, 14))
            c = self.card(sf)
            c.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(c, "БЫСТРЫЕ ДЕЙСТВИЯ", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 8))
            br = ctk.CTkFrame(c, fg_color="transparent")
            br.pack(fill="x", padx=14, pady=(0, 6))
            for i, (t, cmd, k) in enumerate([("ПРИМЕНИТЬ ВЫБРАННОЕ", self.apply_selected, "accent"), ("ВЫБРАТЬ БЕЗОПАСНЫЕ ВЕЗДЕ", self.select_safe_all, "ghost"),
                                             ("ТОЧКА ВОССТАНОВЛЕНИЯ", self.restore_point, "ghost"), ("ЯРЛЫК С ИКОНКОЙ W", self.make_shortcut, "ghost")]):
                br.grid_columnconfigure(i % 2, weight=1, uniform="b")
                self.btn(br, t, cmd, k).grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)
            self.label(c, "Перед массовым применением создайте точку восстановления. Применяется всё, что отмечено во всех разделах.", 11, False, th["muted"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(2, 14))
            c2 = self.card(sf)
            c2.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(c2, "ЖУРНАЛ", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
            self.logbox = ctk.CTkTextbox(c2, height=260, fg_color=th["bg"], text_color=th["muted"], font=ctk.CTkFont(family="Consolas", size=12),
                                         corner_radius=self.R, border_width=0)
            self.logbox.pack(fill="x", padx=14, pady=(0, 14))
            self.logbox.insert("end", "\n".join(self.log_lines) + ("\n" if self.log_lines else ""))
            self.logbox.configure(state="disabled")

        # ---------- разделы с твиками ----------
        def page_tweaks(self, sec):
            th = self.th
            self.cur_sec = sec
            self.body.grid_rowconfigure(1, weight=1)
            self.body.grid_columnconfigure(0, weight=1)
            bar = ctk.CTkFrame(self.body, fg_color="transparent")
            bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            e = ctk.CTkEntry(bar, textvariable=self.query, placeholder_text="ПОИСК ПО ВСЕМ ТВИКАМ", width=300, height=36,
                             fg_color=th["card"], border_color=th["line"], text_color=th["text"], corner_radius=self.R)
            e.pack(side="right")
            e.bind("<KeyRelease>", lambda _e: self._debounce())
            self.list_holder = ctk.CTkFrame(self.body, fg_color="transparent")
            self.list_holder.grid(row=1, column=0, sticky="nsew")
            act = ctk.CTkFrame(self.body, fg_color="transparent")
            act.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            self.btn(act, "ПРИМЕНИТЬ ВЫБРАННОЕ", self.apply_selected, "accent", width=260, height=42).pack(side="left")
            self.btn(act, "ВЫБРАТЬ БЕЗОПАСНЫЕ", self.select_safe, "ghost", width=190, height=42).pack(side="left", padx=8)
            self.btn(act, "СНЯТЬ ВЫБОР", self.clear_section, "ghost", width=140, height=42).pack(side="left")
            self.render_list()

        def _debounce(self):
            if self.search_job:
                self.after_cancel(self.search_job)
            self.search_job = self.after(250, self.render_list)

        def render_list(self):
            if not self.alive(getattr(self, "list_holder", None)):
                return
            for w in self.list_holder.winfo_children():
                w.destroy()
            self.status = {}
            q = self.query.get().strip().lower()
            if q:
                items = [t for t in TWEAKS if q in t.name.lower() or q in t.desc.lower()]
            else:
                items = [t for t in TWEAKS if t.section == self.cur_sec]
            sf = self.scroll(self.list_holder)
            if not items:
                self.label(sf, "Ничего не найдено", 13, False, self.th["muted"]).pack(pady=30)
                return
            for t in items[:40]:
                self._row(sf, t)
            if len(items) > 40:
                self.label(sf, f"Показаны первые 40 из {len(items)} — уточните запрос", 11, False, self.th["muted"]).pack(pady=8)

        def _row(self, parent, t):
            th = self.th
            compact = bool(self.ui["compact"])
            f = ctk.CTkFrame(parent, fg_color=th["card"], corner_radius=self.R, border_width=1, border_color=th["line"])
            f.pack(fill="x", pady=3 if compact else 4, padx=(0, 6))
            f.grid_columnconfigure(1, weight=1)
            sw = ctk.CTkSwitch(f, text="", variable=self.vars[t.id], width=46, progress_color=th["accent"], fg_color=th["line"],
                               button_color="#ffffff", button_hover_color="#e6e6e6")
            sw.grid(row=0, column=0, rowspan=2, padx=(14, 6), pady=6 if compact else 10)
            tf = ctk.CTkFrame(f, fg_color="transparent")
            tf.grid(row=0, column=1, sticky="w", pady=(6 if compact else 10, 0))
            self.label(tf, t.name, 13, True).pack(side="left")
            if t.risky:
                self.label(tf, "   РИСКОВАННО", 11, True, th["bad"]).pack(side="left")
            if self.ui["desc"]:
                self.label(f, t.desc, 11, False, th["muted"], anchor="w", justify="left", wraplength=520).grid(
                    row=1, column=1, sticky="w", pady=(0, 6 if compact else 10))
            st = self.label(f, "", 11, True, th["muted"], width=110)
            st.grid(row=0, column=2, rowspan=2, padx=14)
            self.status[t.id] = st
            if t.oneshot:
                st.configure(text="РАЗОВО")
            else:
                on = is_active(t, self.st)
                st.configure(text="● АКТИВНО" if on else "○ ВЫКЛЮЧЕНО", text_color=th["ok"] if on else th["muted"])

        def select_safe(self):
            sec = getattr(self, "cur_sec", None)
            for t in TWEAKS:
                if (sec is None or t.section == sec) and not t.risky and not t.oneshot:
                    self.vars[t.id].set(1)

        def select_safe_all(self):
            for t in TWEAKS:
                if not t.risky and not t.oneshot:
                    self.vars[t.id].set(1)
            self.log("Выбраны все безопасные твики. Нажмите «Применить выбранное».")

        def clear_section(self):
            sec = getattr(self, "cur_sec", None)
            for t in TWEAKS:
                if sec is None or t.section == sec:
                    self.vars[t.id].set(0)

        # ---------- применение ----------
        def apply_selected(self):
            if self.busy:
                self.log("Подождите, предыдущая операция ещё идёт.")
                return
            jobs = []
            for t in TWEAKS:
                want, cur = bool(self.vars[t.id].get()), is_active(t, self.st)
                if t.oneshot and want:
                    jobs.append(("run", t))
                elif want and not cur:
                    jobs.append(("apply", t))
                elif not want and cur:
                    jobs.append(("revert", t))
            if not jobs:
                self.log("Нечего применять: состояние уже совпадает с выбором.")
                return
            risky = [t.name for k, t in jobs if k in ("apply", "run") and t.risky]
            if risky and not messagebox.askyesno(APP, "Среди выбранного есть рискованные твики:\n\n• " + "\n• ".join(risky) + "\n\nПродолжить?"):
                return
            self.busy = True
            threading.Thread(target=self._worker, args=(jobs,), daemon=True).start()

        def _worker(self, jobs):
            for kind, t in jobs:
                try:
                    if kind == "revert":
                        self.log(f"↩ Откат: {t.name}")
                        revert_tweak(t, self.st, self.log)
                    else:
                        self.log(f"▶ {'Выполняю' if kind == 'run' else 'Применяю'}: {t.name}")
                        apply_tweak(t, self.st, self.log)
                    if t.oneshot:
                        self.q.put(functools.partial(self.vars[t.id].set, 0))
                except Exception as e:
                    self.log(f"   ✖ ошибка: {e}")
            self.log("Готово. Для части твиков нужна перезагрузка.")
            self.q.put("__DONE__")

        def restore_point(self):
            if self.busy:
                return
            self.busy = True
            self.log("Создаю точку восстановления…")

            def w():
                code, out = create_restore_point()
                self.log("Точка восстановления создана." if code == 0 else "Не удалось создать точку (защита системы выключена или лимит 1 раз в 24 ч).")
                self.q.put("__DONE__")
            threading.Thread(target=w, daemon=True).start()

        def make_shortcut(self):
            ok = create_desktop_shortcut()
            self.log("Ярлык с иконкой W создан на рабочем столе." if ok else "Не удалось создать ярлык.")

        # ---------- прицелы ----------
        def _clickthrough(self, w):
            try:
                from ctypes import wintypes
                u = ctypes.windll.user32
                u.GetParent.argtypes, u.GetParent.restype = [wintypes.HWND], wintypes.HWND
                u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_long
                u.SetWindowLongW.argtypes, u.SetWindowLongW.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long
                hwnd = u.GetParent(w.winfo_id()) or w.winfo_id()
                ex = u.GetWindowLongW(hwnd, -20)
                # LAYERED | TRANSPARENT (клики насквозь) | TOOLWINDOW | NOACTIVATE
                u.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000)
            except Exception:
                pass

        def cross_show(self):
            if self.alive(self.cross_win):
                self.cross_apply()
                return
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.configure(bg=CROSS_KEY)
            w.attributes("-topmost", True)
            w.attributes("-transparentcolor", CROSS_KEY)
            self.cross_cv = tk.Canvas(w, width=240, height=240, bg=CROSS_KEY, highlightthickness=0, bd=0)
            self.cross_cv.pack()
            self.cross_win = w
            self.cross_apply()
            if not self._cross_loop:
                self._cross_loop = True
                self.after(1500, self.cross_keep)

        def cross_hide(self):
            if self.alive(self.cross_win):
                self.cross_win.destroy()
            self.cross_win = None

        def cross_apply(self):
            w = self.cross_win
            if not self.alive(w):
                return
            c = self.cross
            x = w.winfo_screenwidth() // 2 - 120 + int(c["ox"])
            y = w.winfo_screenheight() // 2 - 120 + int(c["oy"])
            w.geometry("240x240+%d+%d" % (x, y))
            w.attributes("-alpha", max(0.2, min(1.0, int(c["alpha"]) / 100.0)))
            draw_crosshair(self.cross_cv, 120, 120, c)
            w.update_idletasks()
            self._clickthrough(w)

        def cross_keep(self):
            """Раз в 1.5 с возвращает прицел поверх всех окон."""
            if self.alive(self.cross_win) and self.cross.get("on"):
                try:
                    self.cross_win.attributes("-topmost", True)
                    self.cross_win.lift()
                except Exception:
                    pass
                self.after(1500, self.cross_keep)
            else:
                self._cross_loop = False

        def cross_preview(self):
            cv = getattr(self, "cross_prev", None)
            if self.alive(cv):
                draw_crosshair(cv, 100, 100, self.cross)

        def cross_set(self, **kw):
            self.cross.update(kw)
            save_cross(self.cross)
            if "on" in kw:
                (self.cross_show if self.cross["on"] else self.cross_hide)()
                if self.alive(self.pill_lbl):
                    self.pill_lbl.configure(text=self.pill_text("cross"))
            elif self.cross["on"]:
                self.cross_apply()
            self.cross_preview()

        def cross_toggle(self):
            self.cross_set(on=bool(self.cross_on_var.get()))

        def cross_reset(self):
            keep = self.cross["on"]
            self.cross.update(CROSS_DEFAULT)
            self.cross["on"] = keep
            save_cross(self.cross)
            if keep:
                self.cross_apply()
            self.show("cross")

        def cross_slider(self, parent, text, key, lo, hi):
            th = self.th
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=4)
            self.label(row, text, 12, False, th["muted"], width=140, anchor="w").pack(side="left")
            val = self.label(row, str(self.cross[key]), 12, True, width=44, anchor="e")
            val.pack(side="right")

            def cb(v):
                v = int(round(v))
                val.configure(text=str(v))
                self.cross_set(**{key: v})
            sl = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=hi - lo, command=cb, height=16,
                               progress_color=th["accent"], button_color=th["accent"],
                               button_hover_color=th["accent_h"], fg_color=th["card2"])
            sl.set(self.cross[key])
            sl.pack(side="left", fill="x", expand=True, padx=10)

        def page_cross(self):
            th = self.th
            c = self.cross
            sf = self.scroll(self.body)

            top = self.card(sf)
            top.pack(fill="x", pady=(0, 12), padx=(0, 6))
            hr = ctk.CTkFrame(top, fg_color="transparent")
            hr.pack(fill="x", padx=18, pady=(14, 4))
            self.label(hr, "ПРИЦЕЛ НА ЭКРАНЕ", 11, True, th["muted"]).pack(side="left")
            self.cross_on_var = tk.IntVar(value=1 if c["on"] else 0)
            ctk.CTkSwitch(hr, text="", variable=self.cross_on_var, width=46, progress_color=th["accent"], fg_color=th["line"],
                          button_color="#ffffff", button_hover_color="#e6e6e6", command=self.cross_toggle).pack(side="right")
            self.label(top, "Рисуется отдельным окном поверх игры по центру экрана, клики проходят сквозь него. Работает в режиме "
                             "«Окно без рамки» (Windowed Fullscreen) — в настоящем «Полном экране» оверлей не виден. "
                             "В игру ничего не внедряется.", 11, False, th["muted"], anchor="w", justify="left",
                       wraplength=720).pack(fill="x", padx=18, pady=(0, 14))

            pr = self.card(sf)
            pr.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(pr, "ГОТОВЫЕ ПРИЦЕЛЫ", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
            pf = ctk.CTkFrame(pr, fg_color="transparent")
            pf.pack(fill="x", padx=14, pady=(0, 12))
            for i, (name, p) in enumerate(CROSS_PRESETS.items()):
                pf.grid_columnconfigure(i % 3, weight=1, uniform="cp")
                self.btn(pf, name.upper(), lambda p=p: (self.cross_set(**p), self.show("cross")), "ghost", height=34).grid(
                    row=i // 3, column=i % 3, sticky="ew", padx=4, pady=4)

            card = self.card(sf)
            card.pack(fill="x", pady=(0, 12), padx=(0, 6))
            card.grid_columnconfigure(1, weight=1)
            self.label(card, "ПРЕДПРОСМОТР", 11, True, th["muted"], anchor="w").grid(row=0, column=0, padx=18, pady=(14, 6), sticky="w")
            self.cross_prev = tk.Canvas(card, width=200, height=200, bg="#3a3f4b", highlightthickness=0)
            self.cross_prev.grid(row=1, column=0, padx=18, pady=(0, 16), sticky="n")
            ctl = ctk.CTkFrame(card, fg_color="transparent")
            ctl.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(8, 18), pady=(12, 14))

            om = ctk.CTkOptionMenu(ctl, values=CROSS_STYLES, width=220, height=36, fg_color=th["card2"], button_color=th["line"],
                                   button_hover_color=th["muted"], text_color=th["text"], dropdown_fg_color=th["card"],
                                   dropdown_text_color=th["text"], dropdown_hover_color=th["card2"], corner_radius=self.R,
                                   font=self.F(12, True), dropdown_font=self.F(12), command=lambda v: self.cross_set(style=v))
            om.set(c["style"])
            om.pack(anchor="w")

            sw = ctk.CTkFrame(ctl, fg_color="transparent")
            sw.pack(fill="x", pady=(10, 2))
            for hx in CROSS_COLORS:
                cv = tk.Canvas(sw, width=28, height=28, bg=th["card"], highlightthickness=0, cursor="hand2")
                cv.create_oval(3, 3, 25, 25, fill=hx, width=2, outline=th["text"] if hx == str(c["color"]).lower() else th["card"])
                cv.pack(side="left", padx=(0, 6))
                cv.bind("<Button-1>", lambda _e, h=hx: (self.cross_set(color=h), self.show("cross")))
            self.hex_var = tk.StringVar(value=c["color"])
            he = ctk.CTkEntry(sw, textvariable=self.hex_var, width=92, height=28, fg_color=th["card2"], border_color=th["line"],
                              text_color=th["text"], corner_radius=self.R, placeholder_text="#RRGGBB")
            he.pack(side="left", padx=(8, 0))
            he.bind("<KeyRelease>", lambda _e: valid_hex(self.hex_var.get()) and self.cross_set(color=self.hex_var.get().strip().lower()))

            for text, key, lo, hi in [("РАЗМЕР", "size", 2, 60), ("ТОЛЩИНА", "thick", 1, 8), ("ЗАЗОР В ЦЕНТРЕ", "gap", 0, 30),
                                      ("ПРОЗРАЧНОСТЬ, %", "alpha", 20, 100), ("СМЕЩЕНИЕ X", "ox", -300, 300), ("СМЕЩЕНИЕ Y", "oy", -300, 300)]:
                self.cross_slider(ctl, text, key, lo, hi)

            cb = ctk.CTkFrame(ctl, fg_color="transparent")
            cb.pack(fill="x", pady=(6, 0))
            self.cross_outline = tk.IntVar(value=1 if c["outline"] else 0)
            self.cross_dot = tk.IntVar(value=1 if c["dot"] else 0)
            for text, var, key in [("Чёрный контур", self.cross_outline, "outline"), ("Точка в центре", self.cross_dot, "dot")]:
                ctk.CTkCheckBox(cb, text=text, variable=var, fg_color=th["accent"], hover_color=th["accent_h"], text_color=th["text"],
                                font=self.F(12), command=lambda v=var, k=key: self.cross_set(**{k: bool(v.get())})).pack(side="left", padx=(0, 18))
            self.btn(ctl, "СБРОСИТЬ ПРИЦЕЛ", self.cross_reset, "ghost", width=190, height=34).pack(anchor="w", pady=(12, 0))
            self.cross_preview()

        # ---------- Potato Graphics ----------
        def page_potato(self):
            th = self.th
            self.cur_sec = POTATO_SEC
            if not hasattr(self, "pt"):
                self.pt = {"res": tk.IntVar(value=70), "fps": tk.StringVar(value="Без лимита"), "view": tk.IntVar(value=1),
                           "perf": tk.IntVar(value=0), "lock": tk.IntVar(value=1),
                           "nv": tk.IntVar(value=1), "reflex": tk.IntVar(value=0),
                           "lod": tk.StringVar(value=list(NV_LOD_OPTS)[0])}
            pt = self.pt
            sf = self.scroll(self.body)

            q = self.card(sf)
            q.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(q, "POTATO В ДВА КЛИКА", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 4))
            self.label(q, "Закройте Fortnite и нажмите «Включить». Настройки ниже по умолчанию уже подходят: 3D-разрешение 70%, без лимита FPS, "
                          "дальность прорисовки сохраняется, файл блокируется, профиль NVIDIA применяется. «Вернуть» откатывает файл настроек игры.",
                       11, False, th["muted"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(0, 8))
            qb = ctk.CTkFrame(q, fg_color="transparent")
            qb.pack(fill="x", padx=14, pady=(0, 14))
            self.btn(qb, "ВКЛЮЧИТЬ POTATO", self.pt_apply, "accent", width=260, height=48).pack(side="left", padx=4)
            self.btn(qb, "ВЕРНУТЬ КАК БЫЛО", self.pt_restore, "ghost", width=230, height=48).pack(side="left", padx=4)

            c = self.card(sf)
            c.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(c, "ТОНКАЯ НАСТРОЙКА (НЕОБЯЗАТЕЛЬНО)", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 4))
            found = os.path.exists(FN_INI)
            self.label(c, "● Файл настроек найден" if found else "● GameUserSettings.ini не найден — запустите Fortnite один раз",
                       11, True, th["ok"] if found else th["bad"], anchor="w").pack(fill="x", padx=18, pady=(0, 8))

            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            self.label(row, "3D-РАЗРЕШЕНИЕ", 12, False, th["muted"], width=150, anchor="w").pack(side="left")
            val = self.label(row, "%d%%" % pt["res"].get(), 12, True, width=56, anchor="e")
            val.pack(side="right")

            def on_res(v):
                v = int(round(v))
                pt["res"].set(v)
                val.configure(text="%d%%" % v)
            sl = ctk.CTkSlider(row, from_=30, to=100, number_of_steps=70, command=on_res, height=16, progress_color=th["accent"],
                               button_color=th["accent"], button_hover_color=th["accent_h"], fg_color=th["card2"])
            sl.set(pt["res"].get())
            sl.pack(side="left", fill="x", expand=True, padx=10)

            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            self.label(row, "ЛИМИТ FPS", 12, False, th["muted"], width=150, anchor="w").pack(side="left")
            ctk.CTkOptionMenu(row, values=["Без лимита", "60", "120", "144", "165", "240"], variable=pt["fps"], width=160, height=34,
                              fg_color=th["card2"], button_color=th["line"], button_hover_color=th["muted"], text_color=th["text"],
                              dropdown_fg_color=th["card"], dropdown_text_color=th["text"], dropdown_hover_color=th["card2"],
                              corner_radius=self.R, font=self.F(12, True), dropdown_font=self.F(12)).pack(side="left", padx=10)

            for text, key in [("Оставить дальность прорисовки на «Эпик» (видно врагов издалека)", "view"),
                              ("Режим рендера Performance (ES31) — самый лёгкий", "perf"),
                              ("Заблокировать файл (только чтение), чтобы игра не сбросила настройки", "lock"),
                              ("Также выставить настройки NVIDIA для Fortnite (профиль драйвера через NVIDIA Profile Inspector)", "nv"),
                              ("В Fortnite включён NVIDIA Reflex — Low Latency поставить в Off", "reflex")]:
                ctk.CTkCheckBox(c, text=text, variable=pt[key], fg_color=th["accent"], hover_color=th["accent_h"],
                                text_color=th["text"], font=self.F(12)).pack(anchor="w", padx=18, pady=4)

            lr = ctk.CTkFrame(c, fg_color="transparent")
            lr.pack(fill="x", padx=18, pady=4)
            self.label(lr, "NVIDIA: LOD BIAS (DX)", 12, False, th["muted"], width=170, anchor="w").pack(side="left")
            ctk.CTkOptionMenu(lr, values=list(NV_LOD_OPTS), variable=pt["lod"], width=260, height=34,
                              fg_color=th["card2"], button_color=th["line"], button_hover_color=th["muted"], text_color=th["text"],
                              dropdown_fg_color=th["card"], dropdown_text_color=th["text"], dropdown_hover_color=th["card2"],
                              corner_radius=self.R, font=self.F(12, True), dropdown_font=self.F(12)).pack(side="left", padx=10)

            br = ctk.CTkFrame(c, fg_color="transparent")
            br.pack(fill="x", padx=14, pady=(10, 6))
            self.btn(br, "ОТКРЫТЬ ПАПКУ", self.pt_open, "ghost", width=160, height=40).pack(side="left", padx=4)
            self.label(c, "Закройте Fortnite перед применением. Все остальные настройки файла остаются как были, перед первым "
                          "изменением создаётся бэкап.", 11, False, th["muted"], anchor="w", justify="left",
                       wraplength=700).pack(fill="x", padx=18, pady=(2, 14))

            n = self.card(sf)
            n.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(n, "NVIDIA PROFILE INSPECTOR — ПРОФИЛЬ FORTNITE", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
            self.label(n, "При нажатии «Применить Potato» (с отмеченной галочкой NVIDIA) в профиль Fortnite драйвера записывается:\n"
                          "• Antialiasing Mode — Off · Transparency Supersampling — Off / Multisampling\n"
                          "• Anisotropic filtering mode — User-defined · setting — Off\n"
                          "• Negative LOD bias — Allow · Quality — High performance · Trilinear optimization — On · LOD Bias (DX) — 0x00000000\n"
                          "• Power management — Prefer maximum performance · Threaded optimization — On\n"
                          "• Vertical Sync — Force off · Low Latency — On (Off, если отмечен Reflex)\n"
                          "LOD Bias (DX) можно сменить в списке выше (0x08 / 0x18): тогда Transparency Supersampling ставится в AA_MODE_REPLAY_MODE_ALL — "
                          "без этого драйвер LOD не применяет. По умолчанию значения не меняются (0).",
                       11, False, th["text"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(0, 6))
            self.nv_lbl = self.label(n, "", 11, True, th["muted"], anchor="w")
            self.nv_lbl.pack(fill="x", padx=18, pady=(0, 6))
            nb = ctk.CTkFrame(n, fg_color="transparent")
            nb.pack(fill="x", padx=14, pady=(0, 6))
            self.btn(nb, "СКАЧАТЬ INSPECTOR", self.nv_download_click, "ghost", width=230, height=40).pack(side="left", padx=4)
            self.btn(nb, "ИМПОРТИРОВАТЬ КОНФИГ В INSPECTOR", self.nv_apply_click, "accent", width=320, height=40).pack(side="left", padx=4)
            nb2 = ctk.CTkFrame(n, fg_color="transparent")
            nb2.pack(fill="x", padx=14, pady=(0, 6))
            self.btn(nb2, "УКАЗАТЬ ПУТЬ К INSPECTOR", self.nv_pick, "ghost", width=230, height=32).pack(side="left", padx=4)
            self.btn(nb2, "ОТКРЫТЬ .NIP В INSPECTOR", self.nv_open_nip, "ghost", width=220, height=32).pack(side="left", padx=4)
            self.btn(nb2, "СТРАНИЦА ЗАГРУЗКИ", lambda: __import__("webbrowser").open(NV_RELEASES), "ghost", width=190, height=32).pack(side="left", padx=4)
            self.label(n, "Inspector — сторонняя утилита (Orbmu2k, GitHub): «Скачать Inspector» загрузит её в папку программы сама. Перед импортом "
                          "программа просит Inspector выгрузить ваши текущие изменённые профили (бэкап в папке программы). Чтобы вернуть профиль Fortnite "
                          "к заводскому, в Inspector выберите Fortnite и нажмите «Restore profile to NVIDIA defaults». Работает только с видеокартами NVIDIA.",
                       11, False, th["muted"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(2, 14))
            self.nv_refresh_lbl()

            hb = ctk.CTkFrame(sf, fg_color="transparent")
            hb.pack(fill="x", pady=(0, 6), padx=(0, 6))
            self.label(hb, "ТВИКИ NVIDIA И FORTNITE", 11, True, th["muted"]).pack(side="left", padx=4)
            self.btn(hb, "ПРИМЕНИТЬ ВЫБРАННОЕ", self.apply_selected, "accent", width=210, height=34).pack(side="right")
            self.btn(hb, "ВЫБРАТЬ БЕЗОПАСНЫЕ", self.select_safe, "ghost", width=190, height=34).pack(side="right", padx=8)
            for t in TWEAKS:
                if t.section == POTATO_SEC:
                    self._row(sf, t)

        def pt_apply(self):
            if fortnite_running() and not messagebox.askyesno(
                    APP, "Fortnite сейчас запущен — при выходе он перезапишет настройки.\nЗакройте игру и повторите.\n\nВсё равно применить?"):
                return
            txt = self.pt["fps"].get()
            fps = 0 if txt == "Без лимита" else int(txt)
            try:
                ok, msg = potato_apply(self.pt["res"].get(), fps, bool(self.pt["view"].get()),
                                       bool(self.pt["perf"].get()), bool(self.pt["lock"].get()))
            except Exception as e:
                ok, msg = False, "Ошибка: %s" % e
            self.log(("✔ " if ok else "✖ ") + msg)
            if self.pt["nv"].get():
                self.nv_apply_click()

        def nv_refresh_lbl(self):
            p = nv_find_inspector(self.st.get("nvpi"))
            if self.alive(getattr(self, "nv_lbl", None)):
                self.nv_lbl.configure(text=("● Inspector найден: " + p) if p else "● Inspector не найден — нажмите «Скачать Inspector»",
                                      text_color=self.th["ok"] if p else self.th["warn"])

        def nv_download_click(self):
            def w():
                self.log("Скачиваю NVIDIA Profile Inspector с GitHub…")
                p, msg = nv_download()
                if p:
                    self.st["nvpi"] = p
                    save_state(self.st)
                    self.q.put(self.nv_refresh_lbl)
                self.log(("✔ " if p else "✖ ") + msg)
            threading.Thread(target=w, daemon=True).start()

        def nv_pick(self):
            from tkinter import filedialog
            p = filedialog.askopenfilename(title="nvidiaProfileInspector.exe", filetypes=[("nvidiaProfileInspector", "*.exe")])
            if p:
                self.st["nvpi"] = p
                save_state(self.st)
                self.nv_refresh_lbl()

        def nv_open_nip(self):
            p = nv_write_nip(bool(self.pt["reflex"].get()), NV_LOD_OPTS[self.pt["lod"].get()])
            try:
                os.startfile(p)
            except OSError:
                os.startfile(os.path.dirname(p))

        def nv_apply_click(self):
            reflex = bool(self.pt["reflex"].get())
            lod = NV_LOD_OPTS[self.pt["lod"].get()]

            def w():
                if not nv_has_nvidia():
                    self.log("✖ Видеокарта NVIDIA не найдена — настройки драйвера пропущены.")
                    return
                exe = nv_find_inspector(self.st.get("nvpi"))
                if not exe:
                    self.log("Inspector не найден — скачиваю автоматически…")
                    exe, dmsg = nv_download()
                    if exe:
                        self.st["nvpi"] = exe
                        save_state(self.st)
                        self.q.put(self.nv_refresh_lbl)
                        self.log("✔ " + dmsg)
                    else:
                        self.log("✖ %s Профиль сохранён в %s — скачайте Inspector вручную и повторите." % (dmsg, nv_write_nip(reflex, lod)))
                        return
                self.log("Применяю профиль NVIDIA для Fortnite…")
                try:
                    ok, msg = nv_apply(exe, reflex, lod)
                except Exception as e:
                    ok, msg = False, "Ошибка: %s" % e
                self.log(("✔ " if ok else "✖ ") + msg)
            threading.Thread(target=w, daemon=True).start()

        def pt_restore(self):
            try:
                ok, msg = potato_restore()
            except Exception as e:
                ok, msg = False, "Ошибка: %s" % e
            self.log(("✔ " if ok else "✖ ") + msg)

        def pt_open(self):
            d = os.path.dirname(FN_INI)
            if os.path.isdir(d):
                os.startfile(d)
            else:
                self.log("Папка настроек Fortnite не найдена — запустите игру один раз.")

        # ---------- бустер пинга ----------
        def page_ping(self):
            th = self.th
            P = self.ping
            sf = self.scroll(self.body)
            c1 = self.card(sf)
            c1.pack(fill="x", pady=(0, 12), padx=(0, 6))
            top = ctk.CTkFrame(c1, fg_color="transparent")
            top.pack(fill="x", padx=18, pady=(16, 8))
            ctk.CTkOptionMenu(top, values=list(GAMES), variable=self.game_var, width=270, height=40, fg_color=th["card2"],
                              button_color=th["line"], button_hover_color=th["muted"], text_color=th["text"],
                              dropdown_fg_color=th["card"], dropdown_text_color=th["text"], dropdown_hover_color=th["card2"],
                              corner_radius=self.R, font=self.F(12, True), dropdown_font=self.F(12),
                              command=lambda v: self.ping_reset_view()).pack(side="left")
            self.ping_btn = self.btn(top, "ОСТАНОВИТЬ" if P["running"] else "ЗАПУСТИТЬ ПРОВЕРКУ", self.ping_toggle,
                                     "bad" if P["running"] else "accent", width=250, height=42)
            self.ping_btn.pack(side="right")
            st = ctk.CTkFrame(c1, fg_color="transparent")
            st.pack(fill="x", padx=14, pady=(4, 16))
            self.step_lbls, self.step_cells = [], []
            for i, name in enumerate(STEPS):
                st.grid_columnconfigure(i, weight=1, uniform="s")
                cell = ctk.CTkFrame(st, fg_color=th["card2"], corner_radius=self.R)
                cell.grid(row=0, column=i, padx=4, sticky="ew")
                lb = self.label(cell, name, 11, True, th["muted"])
                lb.pack(pady=15)
                self.step_lbls.append(lb)
                self.step_cells.append(cell)
            c2 = self.card(sf)
            c2.pack(fill="x", pady=(0, 12), padx=(0, 6))
            hr = ctk.CTkFrame(c2, fg_color="transparent")
            hr.pack(fill="x", padx=18, pady=(14, 0))
            self.label(hr, "ИГРОВОЙ МАРШРУТ", 11, True, th["muted"]).pack(side="left")
            self.route_state = self.label(hr, "", 11, True, th["ok"])
            self.route_state.pack(side="right")
            self.canvas = tk.Canvas(c2, width=700, height=230, bg=th["card"], highlightthickness=0)
            self.canvas.pack(pady=(4, 14))
            c3 = self.card(sf)
            c3.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(c3, "ВСЕ УЗЛЫ РЕГИОНА", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
            self.res_frame = ctk.CTkFrame(c3, fg_color="transparent")
            self.res_frame.pack(fill="x", padx=18, pady=(0, 14))
            c4 = self.card(sf)
            c4.pack(fill="x", pady=(0, 12), padx=(0, 6))
            dh = ctk.CTkFrame(c4, fg_color="transparent")
            dh.pack(fill="x", padx=18, pady=(14, 6))
            self.label(dh, "ПОДБОР DNS", 11, True, th["muted"]).pack(side="left")
            self.btn(dh, "СБРОСИТЬ", self.dns_reset_click, "ghost", width=110, height=32).pack(side="right")
            self.btn(dh, "ПРИМЕНИТЬ ЛУЧШИЙ", self.dns_apply_click, "ghost", width=170, height=32).pack(side="right", padx=6)
            self.btn(dh, "ПРОВЕРИТЬ DNS", self.dns_test_click, "accent", width=150, height=32).pack(side="right")
            self.dns_frame = ctk.CTkFrame(c4, fg_color="transparent")
            self.dns_frame.pack(fill="x", padx=18, pady=(0, 14))
            c5 = self.card(sf)
            c5.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(c5, "FORTNITE — ПИНГ В ИГРЕ И DISCORD", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
            r1 = ctk.CTkFrame(c5, fg_color="transparent")
            r1.pack(fill="x", padx=18, pady=(2, 4))
            self.fnp_var = tk.IntVar(value=1 if self.st.get("fn_ping") else 0)
            ctk.CTkSwitch(r1, text="Показывать пинг поверх игры (верхний левый угол)", variable=self.fnp_var, font=self.F(12),
                          text_color=th["text"], progress_color=th["accent"], fg_color=th["line"], button_color="#ffffff",
                          button_hover_color="#e6e6e6", command=self.fnp_toggle).pack(side="left")
            r2 = ctk.CTkFrame(c5, fg_color="transparent")
            r2.pack(fill="x", padx=18, pady=(2, 4))
            self.label(r2, "Регион Fortnite", 12, False, th["muted"]).pack(side="left", padx=(0, 10))
            ctk.CTkOptionMenu(r2, values=[g for g in GAMES if g.startswith("Fortnite")], variable=self.fn_region_var, width=220, height=34,
                              fg_color=th["card2"], button_color=th["line"], button_hover_color=th["muted"], text_color=th["text"],
                              dropdown_fg_color=th["card"], dropdown_text_color=th["text"], dropdown_hover_color=th["card2"],
                              corner_radius=self.R, command=lambda v: self.fnp_pick_host()).pack(side="left")
            r3 = ctk.CTkFrame(c5, fg_color="transparent")
            r3.pack(fill="x", padx=18, pady=(8, 4))
            self.dc_var = tk.IntVar(value=1 if discord_blocked() else 0)
            ctk.CTkSwitch(r3, text="Запретить Discord (брандмауэр + закрыть процесс)", variable=self.dc_var, font=self.F(12),
                          text_color=th["text"], progress_color=th["bad"], fg_color=th["line"], button_color="#ffffff",
                          button_hover_color="#e6e6e6", command=self.dc_toggle).pack(side="left")
            self.label(c5, "Оверлей меряет реальный TCP-пинг до ближайшего дата-центра выбранного региона и работает в режиме «Окно» / «Окно без рамки». "
                           "В полноэкранном режиме Fortnite может его перекрывать — тогда включите штатный показ: Настройки → Game UI → Net Debug Stats = ВКЛ "
                           "(меняется только в лобби/матче). Блокировка Discord действует, пока переключатель включён; после обновления Discord включите её заново.",
                       11, False, th["muted"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(6, 14))
            self.label(sf, "Замеряется реальная задержка (TCP) до дата-центров региона и скорость DNS-серверов, чтобы выбрать лучший регион и DNS. "
                           "Трафик игры через сторонние серверы не перенаправляется — для этого нужен VPN или туннель.",
                       11, False, th["muted"], anchor="w", justify="left", wraplength=720).pack(fill="x", padx=4, pady=(0, 10))
            self.ping_paint_steps()
            self.ping_draw()
            self.ping_render_rows()
            self.dns_render()

        def ping_reset_view(self):
            P = self.ping
            if P["running"]:
                return
            P["results"], P["steps"], P["best"] = {}, [0, 0, 0, 0], None
            self.ping_set_status("ГОТОВ К ПРОВЕРКЕ")
            self.ping_paint_steps()
            self.ping_draw()
            self.ping_render_rows()

        def ping_set_status(self, text):
            self.ping["status"] = text
            if self.page == "ping" and self.alive(self.pill_lbl):
                self.pill_lbl.configure(text=text)

        def ping_paint_steps(self):
            th = self.th
            for i, lb in enumerate(self.step_lbls):
                if not self.alive(lb):
                    continue
                s = self.ping["steps"][i]
                col = {0: th["muted"], 1: th["accent"], 2: th["ok"], -1: th["bad"]}[s]
                lb.configure(text_color=col, text=("✓ " if s == 2 else "… " if s == 1 else "✕ " if s == -1 else "") + STEPS[i])

        def ping_sorted(self):
            res = self.ping["results"]
            ok = sorted([(r, v) for r, v in res.items() if v["avg"] is not None], key=lambda x: x[1]["avg"])
            bad = [(r, v) for r, v in res.items() if v["avg"] is None]
            return ok, bad

        def ping_refresh(self):
            if self.page != "ping":
                return
            self.ping_paint_steps()
            self.ping_draw()
            self.ping_render_rows()

        def ping_draw(self):
            c = getattr(self, "canvas", None)
            if not self.alive(c):
                return
            th = self.th
            P = self.ping
            c.delete("all")
            game = self.game_var.get()
            ok, bad = self.ping_sorted()
            nodes = [(r, v["avg"]) for r, v in ok[:2]]
            for r in GAMES[game]:
                if len(nodes) >= 2:
                    break
                if r not in [n[0] for n in nodes]:
                    nodes.append((r, None))
            ys = [62, 168]

            def rr(x1, y1, x2, y2, r, **kw):
                pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
                return c.create_polygon(pts, smooth=True, **kw)

            c.create_oval(30, 80, 90, 140, outline=th["ok"], width=2)
            c.create_oval(52, 92, 68, 108, outline=th["ok"], width=2)
            c.create_arc(44, 110, 76, 140, start=0, extent=180, style="arc", outline=th["ok"], width=2)
            c.create_text(60, 160, text="ВЫ", fill=th["muted"], font=("Segoe UI", 9, "bold"))
            gx = 600
            rr(gx, 80, gx + 62, 142, 14, fill="#2b6cff", outline="")
            c.create_text(gx + 31, 111, text=game[0], fill="#ffffff", font=("Segoe UI", 26, "bold"))
            c.create_text(gx + 31, 160, text=game.split(" — ")[0].upper(), fill=th["muted"], font=("Segoe UI", 9, "bold"))
            for i, (r, ms) in enumerate(nodes):
                y = ys[i]
                best = i == 0 and ms is not None
                col = th["ok"] if best else th["line"]
                c.create_line(90, 110, 140, 110, 140, y, 255, y, smooth=True, fill=col, width=2)
                c.create_line(425, y, 500, y, 500, 110, gx, 110, smooth=True, fill=col, width=2)
                rr(255, y - 36, 425, y + 36, 12, fill=th["card2"], outline=col, width=2 if best else 1)
                name, cc = REGIONS[r]
                c.create_text(340, y - 18, text=f"{name}  ·  {cc}", fill=th["text"], font=("Segoe UI", 10, "bold"))
                c.create_text(340, y + 8, text=f"{round(ms)} MS" if ms is not None else "— MS", fill=th["ok"] if best else th["muted"], font=("Segoe UI", 11, "bold"))
                if best:
                    c.create_text(340, y + 26, text="ЛУЧШИЙ", fill=th["ok"], font=("Segoe UI", 8, "bold"))
            if len(nodes) == 2:
                c.create_text(340, 115, text="+", fill=th["muted"], font=("Segoe UI", 14, "bold"))
            if self.alive(self.route_state):
                if P["running"]:
                    self.route_state.configure(text="● ПРОВЕРКА", text_color=th["accent"])
                elif P["best"]:
                    self.route_state.configure(text="● МАРШРУТ ВЫБРАН", text_color=th["ok"])
                else:
                    self.route_state.configure(text="● ОЖИДАНИЕ", text_color=th["muted"])

        def ping_render_rows(self):
            fr = getattr(self, "res_frame", None)
            if not self.alive(fr):
                return
            th = self.th
            for w in fr.winfo_children():
                w.destroy()
            ok, bad = self.ping_sorted()
            tested = {r for r, _ in ok + bad}
            pending = [(r, None) for r in GAMES[self.game_var.get()] if r not in tested]
            for i, (r, v) in enumerate(ok + bad + pending):
                best = i == 0 and v is not None and v["avg"] is not None
                row = ctk.CTkFrame(fr, fg_color="transparent")
                row.pack(fill="x", pady=3)
                name, cc = REGIONS[r]
                self.label(row, f"{name}  ({cc})", 12, True, th["ok"] if best else th["text"], width=190, anchor="w").pack(side="left")
                if v is None:
                    txt = "—"
                elif v["avg"] is None:
                    txt = "НЕДОСТУПЕН"
                else:
                    txt = f"{round(v['avg'])} ms   ·   джиттер {round(v['jitter'])}   ·   потери {v['loss']}%"
                self.label(row, txt, 11, False, th["muted"], width=300, anchor="w").pack(side="left")
                bar = ctk.CTkProgressBar(row, width=160, height=8, fg_color=th["card2"], progress_color=th["ok"] if best else th["accent"])
                bar.pack(side="right")
                bar.set(0 if (v is None or v["avg"] is None) else max(0.04, min(1.0, 1 - v["avg"] / 300)))

        def ping_toggle(self):
            P = self.ping
            if P["running"]:
                P["stop"].set()
                self.ping_set_status("ОСТАНОВКА…")
                return
            P["running"] = True
            P["stop"] = threading.Event()
            P["results"], P["steps"], P["best"] = {}, [1, 0, 0, 0], None
            self.ping_set_status("ИДЁТ ПРОВЕРКА")
            if self.alive(self.ping_btn):
                self.ping_btn.configure(text="ОСТАНОВИТЬ", fg_color=self.th["bad"], hover_color=mix(self.th["bad"], "#000000", 0.18), text_color="#ffffff")
            self.ping_refresh()
            threading.Thread(target=self._ping_worker, args=(self.game_var.get(), P["stop"]), daemon=True).start()

        def _ping_worker(self, game, stop):
            P = self.ping
            ui = lambda fn, *a: self.q.put(functools.partial(fn, *a))
            try:
                self._ping_worker_inner(game, stop, ui)
            except Exception as e:
                self.log("✖ Ошибка проверки пинга: %s" % e)
                ui(self._ping_finish, "ОШИБКА ПРОВЕРКИ")

        def _ping_worker_inner(self, game, stop, ui):
            P = self.ping
            base = {"avg": None}
            for h in ("1.1.1.1", "8.8.8.8", "9.9.9.9"):
                base = tcp_ping(h, 443, 3, 1.5, stop)
                if base["avg"] is not None or stop.is_set():
                    break
            if stop.is_set():
                ui(self._ping_finish, "ОСТАНОВЛЕНО")
                return
            if base["avg"] is None:
                P["steps"] = [-1, 0, 0, 0]
                ui(self._ping_finish, "НЕТ ИНТЕРНЕТА")
                return
            P["steps"] = [2, 1, 0, 0]
            ui(self.ping_refresh)
            for r in GAMES[game]:
                if stop.is_set():
                    break
                P["results"][r] = tcp_ping(f"ec2.{r}.amazonaws.com", 443, 5, 1.5, stop)
                ui(self.ping_refresh)
            if stop.is_set():
                ui(self._ping_finish, "ОСТАНОВЛЕНО")
                return
            ok, _ = self.ping_sorted()
            if not ok:
                P["steps"] = [2, -1, 0, 0]
                ui(self._ping_finish, "УЗЛЫ НЕДОСТУПНЫ")
                return
            P["steps"] = [2, 2, 1, 0]
            ui(self.ping_refresh)
            best_r, best_v = ok[0]
            P["best"] = best_r
            P["steps"] = [2, 2, 2, 2]
            quality = "ОТЛИЧНАЯ" if best_v["loss"] == 0 and best_v["jitter"] < 10 else "СРЕДНЯЯ" if best_v["loss"] < 20 else "НЕСТАБИЛЬНАЯ"
            self.log(f"Лучший узел: {REGIONS[best_r][0]} — {round(best_v['avg'])} мс, джиттер {round(best_v['jitter'])}, потери {best_v['loss']}%. Связь: {quality}.")
            ui(self._ping_finish, f"ЛУЧШИЙ: {REGIONS[best_r][0]} · {round(best_v['avg'])} MS")

        def _ping_finish(self, text):
            P = self.ping
            P["running"] = False
            self.ping_set_status(text)
            if self.alive(getattr(self, "ping_btn", None)) and self.page == "ping":
                self.ping_btn.configure(text="ЗАПУСТИТЬ ПРОВЕРКУ", fg_color=self.th["accent"], hover_color=self.th["accent_h"], text_color=self.th["on_accent"])
            self.ping_refresh()

        # ---------- Fortnite: пинг-оверлей и Discord ----------
        def fnp_pick_host(self):
            regs = GAMES.get(self.fn_region_var.get(), _EU)
            res = self.ping.get("results", {})
            scored = [(res[r]["avg"], r) for r in regs if res.get(r) and res[r].get("avg") is not None]
            r = min(scored)[1] if scored else regs[0]
            self.fnp["host"] = "ec2.%s.amazonaws.com" % r

        def fnp_show(self):
            if self.alive(self.fnp["win"]):
                return
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.configure(bg="#0c0d10")
            w.attributes("-topmost", True)
            try:
                w.attributes("-alpha", 0.88)
            except Exception:
                pass
            lbl = tk.Label(w, text="PING …", font=("Consolas", 12, "bold"), fg="#34e0a1", bg="#0c0d10", padx=10, pady=4)
            lbl.pack()
            w.geometry("+12+12")
            self.fnp["win"], self.fnp["lbl"] = w, lbl
            w.update_idletasks()
            self._clickthrough(w)
            self.fnp_pick_host()
            if not self.fnp["run"]:
                self.fnp["run"] = True
                threading.Thread(target=self._fnp_worker, daemon=True).start()
            if not self.fnp["keep"]:
                self.fnp["keep"] = True
                self.after(1500, self.fnp_keep)

        def fnp_hide(self):
            self.fnp["run"] = False
            if self.alive(self.fnp["win"]):
                self.fnp["win"].destroy()
            self.fnp["win"] = None

        def fnp_keep(self):
            if self.alive(self.fnp["win"]) and self.st.get("fn_ping"):
                try:
                    self.fnp["win"].attributes("-topmost", True)
                    self.fnp["win"].lift()
                except Exception:
                    pass
                self.after(1500, self.fnp_keep)
            else:
                self.fnp["keep"] = False

        def fnp_update(self, text, color):
            if self.alive(self.fnp["lbl"]):
                self.fnp["lbl"].configure(text=text, fg=color)

        def _fnp_worker(self):
            import time
            hist = []
            while self.fnp["run"]:
                r = tcp_ping(self.fnp["host"], 443, 1, 1.2)
                hist = (hist + [r["avg"]])[-20:]
                okv = [x for x in hist if x is not None]
                loss = round(100 * (len(hist) - len(okv)) / len(hist))
                if okv:
                    cur = okv[-1] if hist[-1] is not None else None
                    jit = sum(abs(a - b) for a, b in zip(okv, okv[1:])) / (len(okv) - 1) if len(okv) > 1 else 0
                else:
                    cur, jit = None, 0
                th = self.th
                if cur is None:
                    txt, col = "PING —  ·  LOSS %d%%" % loss, th["bad"]
                else:
                    col = th["ok"] if cur < 50 else th["warn"] if cur < 100 else th["bad"]
                    txt = "PING %d ms  ·  JIT %d  ·  LOSS %d%%" % (round(cur), round(jit), loss)
                self.q.put(lambda t=txt, c=col: self.fnp_update(t, c))
                for _ in range(10):
                    if not self.fnp["run"]:
                        break
                    time.sleep(0.1)

        def fnp_toggle(self):
            on = bool(self.fnp_var.get())
            self.st["fn_ping"] = on
            save_state(self.st)
            (self.fnp_show if on else self.fnp_hide)()
            self.log("Пинг поверх игры включён." if on else "Пинг поверх игры выключен.")

        def dc_toggle(self):
            on = bool(self.dc_var.get())

            def w():
                ok, msg = discord_block() if on else discord_unblock()
                self.log(msg)
                if not ok:
                    self.q.put(lambda: self.dc_var.set(0 if on else 1))
            threading.Thread(target=w, daemon=True).start()

        # ---------- конфиги игроков ----------
        def cfg_apply_click(self, p):
            def w():
                ok, plan, msg = fn_cfg_plan(p)
                if not ok:
                    self.log(msg)
                    return
                if fortnite_running():
                    self.log("Закройте Fortnite: при выходе он перезапишет файл настроек.")
                    return
                self.q.put(lambda: self.cfg_confirm(p, plan))
            threading.Thread(target=w, daemon=True).start()

        def cfg_confirm(self, p, plan):
            body = "\n".join("%s: %s → %s" % (k, o, n) for k, o, n in plan[:14])
            if messagebox.askyesno(APP, "Применить конфиг «%s» в Fortnite?\n\n%s\n\nДо записи будет создан бэкап. "
                                        "DPI и частоту опроса мыши программа не меняет — их ставят в ПО мыши." % (p["name"], body)):
                def w():
                    ok, msg = fn_cfg_apply(plan)
                    self.log(msg)
                threading.Thread(target=w, daemon=True).start()

        def cfg_restore_click(self):
            def w():
                self.log(fn_cfg_restore()[1])
            threading.Thread(target=w, daemon=True).start()

        def player_avatar(self, parent, p, size=84):
            th = self.th
            for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                path = os.path.join(PLAYERS_DIR, p["id"] + ext)
                if os.path.exists(path):
                    try:
                        from PIL import Image
                        im = Image.open(path).convert("RGBA")
                        w, h = im.size
                        m = min(w, h)
                        im = im.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
                        img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
                        self._photos.append(img)
                        return ctk.CTkLabel(parent, text="", image=img, width=size, height=size)
                    except Exception:
                        pass
            return ctk.CTkLabel(parent, text=p["name"][:2].upper(), width=size, height=size, fg_color=th["card2"],
                                text_color=th["accent"], corner_radius=self.R, font=self.F(24, True))

        def page_configs(self):
            th = self.th
            ensure_players_file()
            self._photos = []
            players = load_players()
            sf = self.scroll(self.body)
            top = self.card(sf)
            top.pack(fill="x", pady=(0, 12), padx=(0, 6))
            self.label(top, "КОНФИГИ ИГРОКОВ FORTNITE", 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 4))
            self.label(top, "Мышь и чувствительность из открытых источников — у разных сайтов значения расходятся и со временем меняются, "
                            "поэтому сверяйте с prosettings.net. Фото в программу не вшиты: положите картинку <id>.png (или .jpg) в папку с фото — "
                            "она появится рядом с игроком. Новых игроков добавляйте в players.json. «Применить в Fortnite» меняет только те ключи чувствительности, которые уже есть в вашем GameUserSettings.ini; перед записью показывается список и делается бэкап.",
                       11, False, th["muted"], anchor="w", justify="left", wraplength=700).pack(fill="x", padx=18, pady=(0, 8))
            br = ctk.CTkFrame(top, fg_color="transparent")
            br.pack(fill="x", padx=18, pady=(0, 14))
            self.btn(br, "ПАПКА С ФОТО", lambda: os.startfile(PLAYERS_DIR), "ghost", width=150, height=34).pack(side="left", padx=(0, 8))
            self.btn(br, "ОТКРЫТЬ players.json", lambda: os.startfile(PLAYERS_FILE), "ghost", width=180, height=34).pack(side="left", padx=(0, 8))
            self.btn(br, "ОБНОВИТЬ", self.refresh_page, "ghost", width=110, height=34).pack(side="left", padx=(0, 8))
            self.btn(br, "ОТКАТ В FORTNITE", self.cfg_restore_click, "bad", width=170, height=34).pack(side="left")

            def copy(txt):
                self.clipboard_clear()
                self.clipboard_append(txt)
                self.log("Конфиг скопирован в буфер обмена.")

            def val(v, suf=""):
                return "—" if v in (None, "") else "%g%s" % (v, suf) if isinstance(v, (int, float)) else "%s%s" % (v, suf)

            for p in players:
                c = self.card(sf)
                c.pack(fill="x", pady=(0, 12), padx=(0, 6))
                row = ctk.CTkFrame(c, fg_color="transparent")
                row.pack(fill="x", padx=16, pady=14)
                self.player_avatar(row, p).pack(side="left", padx=(0, 16))
                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True)
                self.label(info, p["name"], 16, True, anchor="w").pack(fill="x")
                if p.get("team"):
                    self.label(info, p["team"], 11, False, th["muted"], anchor="w").pack(fill="x")
                try:
                    edpi = "%.1f" % (float(p["dpi"]) * float(p["x"]) / 100.0)
                except Exception:
                    edpi = "—"
                self.label(info, "DPI %s · Hz %s · eDPI %s · Sens X/Y %s / %s" % (
                    val(p.get("dpi")), val(p.get("hz")), edpi, val(p.get("x"), "%"), val(p.get("y"), "%")), 12, False, anchor="w").pack(fill="x", pady=(4, 0))
                self.label(info, "Targeting %s · Scope %s · Build %s · Edit %s · Разрешение %s" % (
                    val(p.get("target"), "%"), val(p.get("scope"), "%"), val(p.get("build"), "%"), val(p.get("edit"), "%"),
                    val(p.get("res"))), 12, False, anchor="w").pack(fill="x")
                if p.get("note"):
                    self.label(info, p["note"], 11, False, th["muted"], anchor="w").pack(fill="x", pady=(2, 0))
                bb = ctk.CTkFrame(row, fg_color="transparent")
            bb.pack(side="right")
            self.btn(bb, "ПРИМЕНИТЬ В FORTNITE", lambda pp=p: self.cfg_apply_click(pp), "accent", width=190, height=34).pack(pady=(0, 6))
            self.btn(bb, "КОПИРОВАТЬ", lambda pp=p: copy(player_text(pp)), "ghost", width=190, height=34).pack()

        # ---------- DNS ----------
        def dns_render(self):
            fr = getattr(self, "dns_frame", None)
            if not self.alive(fr):
                return
            th = self.th
            for w in fr.winfo_children():
                w.destroy()
            res = self.ping["dns"]
            valid = {k: v for k, v in res.items() if v is not None}
            best = min(valid, key=valid.get) if valid else None
            for name, p, s in DNS_SERVERS:
                row = ctk.CTkFrame(fr, fg_color="transparent")
                row.pack(fill="x", pady=2)
                self.label(row, name, 12, True, th["ok"] if name == best else th["text"], width=120, anchor="w").pack(side="left")
                self.label(row, f"{p} / {s}", 11, False, th["muted"], width=240, anchor="w").pack(side="left")
                v = res.get(name, "—")
                txt = "—" if v == "—" else "НЕТ ОТВЕТА" if v is None else f"{round(v)} ms"
                self.label(row, txt, 12, True, th["ok"] if name == best else th["muted"], anchor="w").pack(side="left")

        def dns_test_click(self):
            self.log("Проверяю скорость DNS-серверов…")

            def w():
                for name, p, s in DNS_SERVERS:
                    self.ping["dns"][name] = dns_ping(p)
                    self.q.put(self.dns_render)
                valid = {k: v for k, v in self.ping["dns"].items() if v is not None}
                self.log("Самый быстрый DNS: " + (min(valid, key=valid.get) if valid else "не найден"))
            threading.Thread(target=w, daemon=True).start()

        def dns_apply_click(self):
            valid = {k: v for k, v in self.ping["dns"].items() if v is not None}
            if not valid:
                self.log("Сначала нажмите «Проверить DNS».")
                return
            best = min(valid, key=valid.get)
            p, s = next((a, b) for n, a, b in DNS_SERVERS if n == best)

            def w():
                code, out = set_dns(p, s)
                if code == 0:
                    self.st["dns"] = best
                    save_state(self.st)
                self.log(f"DNS {best} ({p}) применён." if code == 0 else "Не удалось сменить DNS: " + out.strip()[:120])
            threading.Thread(target=w, daemon=True).start()

        def dns_reset_click(self):
            def w():
                code, out = reset_dns()
                if code == 0:
                    self.st.pop("dns", None)
                    save_state(self.st)
                self.log("DNS сброшен на автоматический." if code == 0 else "Не удалось сбросить DNS: " + out.strip()[:120])
            threading.Thread(target=w, daemon=True).start()

        # ---------- внешний вид ----------
        def set_ui(self, **kw):
            self.ui.update(kw)
            save_ui(self.ui)
            self.restyle()

        def restyle(self):
            self.th = build_theme(self.ui)
            ctk.set_appearance_mode("dark" if self.th["dark"] else "light")
            try:
                self.attributes("-alpha", float(self.ui["alpha"]))
            except Exception:
                pass
            self.opt_vars = {}
            self._build()
            self.show(self.page)

        def page_look(self):
            th = self.th
            sf = self.scroll(self.body)

            def section(title):
                c = self.card(sf)
                c.pack(fill="x", pady=(0, 12), padx=(0, 6))
                self.label(c, title, 11, True, th["muted"], anchor="w").pack(fill="x", padx=18, pady=(14, 8))
                inner = ctk.CTkFrame(c, fg_color="transparent")
                inner.pack(fill="x", padx=14, pady=(0, 14))
                return inner

            inner = section("ТЕМА")
            for i, name in enumerate(PRESETS):
                inner.grid_columnconfigure(i % 3, weight=1, uniform="p")
                cur = name == self.ui["preset"]
                ctk.CTkButton(inner, text=name.upper(), height=40, corner_radius=self.R, font=self.F(12, True),
                              fg_color=th["line"] if cur else th["card2"], hover_color=th["line"], text_color=th["text"],
                              border_width=2, border_color=PRESETS[name][1],
                              command=lambda n=name: self.set_ui(preset=n, accent=None)).grid(row=i // 3, column=i % 3, sticky="ew", padx=4, pady=4)

            inner = section("АКЦЕНТНЫЙ ЦВЕТ")
            sw = ctk.CTkFrame(inner, fg_color="transparent")
            sw.pack(fill="x")
            for col in ACCENTS:
                cur = self.th["accent"].lower() == col.lower()
                ctk.CTkButton(sw, text="", width=38, height=38, corner_radius=self.R, fg_color=col, hover_color=col,
                              border_width=3 if cur else 0, border_color=th["text"],
                              command=lambda c=col: self.set_ui(accent=c)).pack(side="left", padx=4, pady=4)
            hr = ctk.CTkFrame(inner, fg_color="transparent")
            hr.pack(fill="x", pady=(8, 0))
            hexv = tk.StringVar(value=self.th["accent"])
            self.opt_vars["hex"] = hexv
            ctk.CTkEntry(hr, textvariable=hexv, width=140, height=36, fg_color=th["card2"], border_color=th["line"],
                         text_color=th["text"], corner_radius=self.R, placeholder_text="#34E0A1").pack(side="left", padx=4)

            def apply_hex():
                v = hexv.get().strip()
                if valid_hex(v):
                    self.set_ui(accent=v)
                else:
                    self.log("Введите цвет в формате #RRGGBB, например #34E0A1.")
            self.btn(hr, "ПРИМЕНИТЬ HEX", apply_hex, "ghost", width=150, height=36).pack(side="left", padx=4)

            inner = section("РАЗМЕР ТЕКСТА И ЗАКРУГЛЕНИЕ")
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x")
            cur_s = next((k for k, v in SCALE_OPTS.items() if abs(v - float(self.ui["scale"])) < 0.01), "Обычный")
            cur_r = next((k for k, v in RAD_OPTS.items() if v == int(self.ui["radius"])), "Средние")
            for lab, vals, cur, cb in [("Текст", list(SCALE_OPTS), cur_s, lambda v: self.set_ui(scale=SCALE_OPTS[v])),
                                       ("Углы", list(RAD_OPTS), cur_r, lambda v: self.set_ui(radius=RAD_OPTS[v]))]:
                self.label(row, lab, 12, False, th["muted"]).pack(side="left", padx=(4, 8))
                ctk.CTkOptionMenu(row, values=vals, width=150, height=36, fg_color=th["card2"], button_color=th["line"],
                                  button_hover_color=th["muted"], text_color=th["text"], dropdown_fg_color=th["card"],
                                  dropdown_text_color=th["text"], dropdown_hover_color=th["card2"], corner_radius=self.R,
                                  command=cb).pack(side="left", padx=(0, 18))
                row.winfo_children()[-1].set(cur)

            inner = section("ОКНО И СПИСОК")
            ar = ctk.CTkFrame(inner, fg_color="transparent")
            ar.pack(fill="x", pady=(0, 8))
            self.label(ar, "Прозрачность окна", 12, False, th["muted"]).pack(side="left", padx=(4, 12))

            def on_alpha(v):
                v = round(float(v), 2)
                self.ui["alpha"] = v
                try:
                    self.attributes("-alpha", v)
                except Exception:
                    pass
            sl = ctk.CTkSlider(ar, from_=0.7, to=1.0, number_of_steps=15, width=260, command=on_alpha,
                               progress_color=th["accent"], button_color=th["accent"], button_hover_color=th["accent_h"], fg_color=th["line"])
            sl.set(float(self.ui["alpha"]))
            sl.pack(side="left")
            sl.bind("<ButtonRelease-1>", lambda _e: save_ui(self.ui))
            for key, text in [("desc", "Показывать описания твиков"), ("compact", "Компактные карточки")]:
                v = tk.IntVar(value=1 if self.ui[key] else 0)
                self.opt_vars[key] = v
                ctk.CTkSwitch(inner, text=text, variable=v, font=self.F(12), text_color=th["text"], progress_color=th["accent"],
                              fg_color=th["line"], button_color="#ffffff", button_hover_color="#e6e6e6",
                              command=lambda k=key, vv=v: self.set_ui(**{k: bool(vv.get())})).pack(anchor="w", padx=4, pady=6)
            self.btn(sf, "СБРОСИТЬ ВНЕШНИЙ ВИД", lambda: (self.ui.update(DEFAULT_UI), save_ui(self.ui), self.restyle()), "ghost", width=240, height=40).pack(anchor="w", pady=(0, 10))

    App().mainloop()


def main():
    if "--make-icon" in sys.argv:
        write_ico("W.ico")
        _say("W.ico created")
        return
    if os.name != "nt" or winreg is None:
        _say("Wexxyy Tweaker works on Windows only.")
        return
    if not is_admin():
        relaunch_as_admin()
        return
    launch_ui()


if __name__ == "__main__":
    main()
