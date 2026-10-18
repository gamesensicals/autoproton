"""What autoproton knows about common launch-option pieces.

Categories:
- ``fix``: a known compatibility fix. Ticked by default when enough reports use it.
- ``arg``: a game argument (``--launcher-skip``). Treated like a fix.
- ``unknown``: an environment variable not in these tables. Needs more votes.
- ``preference``: overlays, performance helpers, upscalers. Never ticked by default.
- ``situational``: depends on the user's screen or setup (HDR, Wayland, resolution).
  Never ticked by default.
- ``debug``: logging. Hidden.
- ``obsolete``: no longer does anything useful on current Proton. Hidden.
"""

from __future__ import annotations

import re

FIX_ENV = {
    "PROTON_USE_WINED3D", "PROTON_USE_WINED3D11", "PROTON_NO_ESYNC", "PROTON_NO_FSYNC", "PROTON_NO_NTSYNC",
    "PROTON_USE_NTSYNC", "PROTON_NO_D3D10", "PROTON_NO_D3D11", "PROTON_NO_D3D12", "PROTON_NO_XIM",
    "PROTON_HEAP_DELAY_FREE", "PROTON_FORCE_LARGE_ADDRESS_AWARE", "PROTON_OLD_GL_STRING", "PROTON_SET_GAME_DRIVE",
    "PROTON_NO_STEAMINPUT", "PROTON_PREFER_SDL", "PROTON_ENABLE_HIDRAW", "PROTON_DISABLE_HIDRAW",
    "PROTON_USE_XALIA", "PROTON_NO_WM_DECORATION", "PROTON_LOCAL_SHADER_CACHE", "PROTON_HIDE_NVIDIA_GPU",
    "PROTON_ENABLE_NVAPI", "PROTON_NVIDIA_LIBS_NO_32BIT", "PROTON_ENABLE_AMD_AGS", "PROTON_USE_WOW64",
    "PROTON_VKD3D_HEAP", "VKD3D_CONFIG", "VKD3D_FEATURE_LEVEL", "VKD3D_SHADER_MODEL", "WINEDLLOVERRIDES",
    "WINE_CPU_TOPOLOGY", "WINE_LARGE_ADDRESS_AWARE", "SDL_AUDIODRIVER", "PULSE_LATENCY_MSEC", "LC_ALL", "LANG",
    "DXVK_ENABLE_NVAPI", "DXVK_NVAPIHACK", "mesa_glthread", "SDL_VIDEO_X11_DGAMOUSE", "SDL_DYNAMIC_API",
    "SteamDeck", "STEAMDECK",
}
PREFERENCE_ENV = {
    "MANGOHUD", "MANGOHUD_CONFIG", "DXVK_HUD", "DXVK_FRAME_RATE", "VKD3D_FRAME_RATE", "PROTON_DLSS_UPGRADE",
    "PROTON_DLSS_INDICATOR", "PROTON_XESS_UPGRADE", "PROTON_FSR3_UPGRADE", "PROTON_FSR4_UPGRADE",
    "PROTON_FSR4_RDNA3_UPGRADE", "ENABLE_VKBASALT", "OBS_VKCAPTURE", "WINE_FULLSCREEN_FSR",
    "WINE_FULLSCREEN_FSR_STRENGTH", "WINE_FULLSCREEN_FSR_MODE", "__GL_SYNC_TO_VBLANK", "vblank_mode",
    "__GL_THREADED_OPTIMIZATIONS", "__GL_SHADER_DISK_CACHE_SIZE", "__GL_SHADER_DISK_CACHE",
    "DXVK_STATE_CACHE", "STAGING_SHARED_MEMORY", "PROTON_ENABLE_NGX_UPDATER", "DXVK_FRAME_PACE",
    "ENABLE_LAYER_MESA_ANTI_LAG", "PROTON_PRIORITY_HIGH",
}
SITUATIONAL_ENV = {
    "PROTON_ENABLE_HDR", "ENABLE_HDR_WSI", "DXVK_HDR", "DISABLE_HDR_WSI", "PROTON_ENABLE_WAYLAND",
    "PROTON_NO_WAYLAND", "SDL_VIDEODRIVER", "ENABLE_GAMESCOPE_WSI", "DISABLE_GAMESCOPE_WSI", "PROTON_FORCE_NVAPI",
    "DRI_PRIME", "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME", "__VK_LAYER_NV_optimus",
    "DXVK_FILTER_DEVICE_NAME", "MESA_VK_DEVICE_SELECT", "PROTON_USE_SDL",
}
DEBUG_ENV = {"PROTON_LOG", "WINEDEBUG", "DXVK_LOG_LEVEL", "VKD3D_DEBUG", "VKD3D_SHADER_DEBUG", "DXVK_LOG_PATH",
             "PROTON_DUMP_DEBUG_COMMANDS", "PROTON_DEBUG_DIR"}
OBSOLETE_ENV = {"DXVK_ASYNC", "PROTON_USE_D9VK", "PROTON_USE_SECCOMP", "PROTON_BATTLEYE_RUNTIME",
                "PROTON_EAC_RUNTIME", "PROTON_NO_GLSL", "PROTON_USE_DXVK", "PROTON_FORCE_LARGE_ADDRESS"}
OBSOLETE_VALUES = {("RADV_PERFTEST", "aco"), ("RADV_PERFTEST", "ACO")}

VENDOR_ENV = {
    "PROTON_ENABLE_NVAPI": "nvidia", "PROTON_HIDE_NVIDIA_GPU": "nvidia", "DXVK_ENABLE_NVAPI": "nvidia",
    "DXVK_NVAPIHACK": "nvidia", "PROTON_DLSS_UPGRADE": "nvidia", "PROTON_DLSS_INDICATOR": "nvidia",
    "PROTON_NVIDIA_LIBS_NO_32BIT": "nvidia", "PROTON_ENABLE_NGX_UPDATER": "nvidia", "PROTON_FORCE_NVAPI": "nvidia",
    "PROTON_FSR4_UPGRADE": "amd", "PROTON_FSR4_RDNA3_UPGRADE": "amd", "PROTON_ENABLE_AMD_AGS": "amd",
    "ENABLE_LAYER_MESA_ANTI_LAG": "amd", "PROTON_XESS_UPGRADE": None,
}
VENDOR_PREFIXES = (("__GL_", "nvidia"), ("__NV_", "nvidia"), ("NV_", "nvidia"), ("RADV_", "amd"),
                   ("ACO_", "amd"), ("AMD_", "amd"), ("ANV_", "intel"), ("INTEL_", "intel"))

WRAPPERS = {
    "gamemoderun": "preference",
    "mangohud": "preference",
    "game-performance": "preference",
    "obs-gamecapture": "preference",
    "strangle": "preference",
    "gamescope": "situational",
    "prime-run": "situational",
    "nvidia-offload": "situational",
}

# Arguments that are about the player's screen or taste rather than about getting the game to run.
SITUATIONAL_ARGS = {
    "-w", "-h", "-width", "-height", "-screen-width", "-screen-height", "-screen-fullscreen", "-window-mode",
    "-windowed", "-window", "-fullscreen", "-full", "-popupwindow", "-noborder", "-borderless", "-refresh",
    "-freq", "+fps_max", "-fps_max", "-fps", "-maxfps", "-monitor", "-adapter", "-modded", "-lang", "-language",
    "-vrmode", "-vr", "-nohmd", "-high", "-threads", "-console", "-dev",
}

DESCRIPTIONS = {
    "gamemoderun": "Feral GameMode: asks the system for more performance while you play.",
    "mangohud": "MangoHud: on-screen FPS and performance overlay.",
    "MANGOHUD": "MangoHud: on-screen FPS and performance overlay.",
    "game-performance": "Switches the power profile to performance while the game runs.",
    "gamescope": "Runs the game inside the gamescope compositor (resolution/upscaling settings are personal).",
    "prime-run": "Runs the game on the dedicated GPU of a hybrid-graphics laptop.",
    "WINEDLLOVERRIDES": "Loads the game's own copies of these DLLs instead of Wine's (often needed for mods and fixes).",
    "PROTON_USE_WINED3D": "Uses OpenGL-based WineD3D instead of DXVK.",
    "PROTON_NO_ESYNC": "Turns off esync (fixes some hangs).",
    "PROTON_NO_FSYNC": "Turns off fsync (fixes some hangs).",
    "PROTON_USE_NTSYNC": "Uses the kernel's NTSYNC driver for Windows-style synchronisation (needs kernel 6.14+).",
    "PROTON_ENABLE_WAYLAND": "Uses Proton's native Wayland driver instead of XWayland.",
    "PROTON_ENABLE_HDR": "HDR output (needs an HDR screen and Wayland or gamescope).",
    "ENABLE_HDR_WSI": "HDR support layer for Vulkan (needs an HDR screen).",
    "DXVK_HDR": "Lets DXVK games output HDR (needs an HDR screen).",
    "PROTON_ENABLE_NVAPI": "Exposes NVIDIA's NVAPI to the game (DLSS, Reflex). NVIDIA only.",
    "PROTON_DLSS_UPGRADE": "Upgrades the game's DLSS to the newest version. NVIDIA only.",
    "PROTON_FSR4_UPGRADE": "Upgrades the game's FSR to FSR 4. AMD RDNA 4 only.",
    "PROTON_LOCAL_SHADER_CACHE": "Keeps a per-game shader cache to cut stutter.",
    "WINE_CPU_TOPOLOGY": "Tells the game how many CPU cores to use (fixes games that crash on many-core CPUs).",
    "PROTON_HEAP_DELAY_FREE": "Works around some games' memory bugs.",
    "PROTON_NO_WM_DECORATION": "Removes window borders that some games draw wrongly.",
    "SDL_AUDIODRIVER": "Chooses the audio backend (fixes missing or crackling sound).",
    "PULSE_LATENCY_MSEC": "Raises the audio buffer (fixes crackling sound).",
    "VKD3D_CONFIG": "DirectX 12 translation options.",
    "-sdlaudiodriver": "Chooses the audio backend (fixes missing or crackling sound).",
    "--launcher-skip": "Skips the game's own launcher.",
    "-skipstartscreen": "Skips the start screen.",
    "--intro-skip": "Skips intro videos.",
    "-novid": "Skips intro videos.",
    "-dx11": "Runs the game in DirectX 11 mode.",
    "-dx12": "Runs the game in DirectX 12 mode.",
    "-vulkan": "Runs the game in Vulkan mode.",
}

CATEGORY_LABELS = {
    "fix": "Fix",
    "arg": "Game option",
    "unknown": "Setting",
    "preference": "Optional extra",
    "situational": "Depends on your setup",
    "debug": "Debugging",
    "obsolete": "Outdated",
}

_RESOLUTION_VALUE = re.compile(r"^\d{3,5}$")


def env_vendor(key: str) -> str | None:
    if key in VENDOR_ENV:
        return VENDOR_ENV[key]
    for prefix, vendor in VENDOR_PREFIXES:
        if key.startswith(prefix):
            return vendor
    return None


def env_category(key: str, value: str | None) -> str:
    if (key, value) in OBSOLETE_VALUES or key in OBSOLETE_ENV:
        return "obsolete"
    if key in DEBUG_ENV:
        return "debug"
    if key in SITUATIONAL_ENV:
        return "situational"
    if key in PREFERENCE_ENV:
        return "preference"
    if key in FIX_ENV:
        return "fix"
    return "unknown"


def arg_category(flag: str, value: str | None) -> str:
    if flag.lower() in SITUATIONAL_ARGS:
        return "situational"
    if value and _RESOLUTION_VALUE.match(value) and any(k in flag.lower() for k in ("width", "height", "res", "w", "h")):
        return "situational"
    return "arg"


def describe(key: str) -> str:
    return DESCRIPTIONS.get(key) or DESCRIPTIONS.get(key.lower()) or ""


# ---- safety --------------------------------------------------------------------

# Variables that would let a launch option run something else entirely, or that
# point at files on the reporter's own machine.
_DENY_ENV = {
    "PATH", "HOME", "USER", "SHELL", "BASH_ENV", "ENV", "IFS", "PS1", "PS2", "PS4", "PROMPT_COMMAND", "TMPDIR",
    "DISPLAY", "WAYLAND_DISPLAY", "WINEPREFIX", "WINE", "WINESERVER", "WINELOADER", "PROTONPATH",
    "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH", "VK_ICD_FILENAMES", "VK_DRIVER_FILES",
    "VK_LAYER_PATH", "VK_ADD_LAYER_PATH", "VK_INSTANCE_LAYERS", "LIBGL_DRIVERS_PATH",
    "__EGL_VENDOR_LIBRARY_FILENAMES", "GCONV_PATH", "GIO_MODULE_DIR", "GTK_PATH", "NODE_OPTIONS",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
    # gamemoderun runs $GAMEMODERUNEXEC in front of the game
    "GAMEMODERUNEXEC",
}
_DENY_PREFIXES = ("LD_", "PYTHON", "PERL", "RUBY", "XDG_", "DBUS_", "STEAM_RUNTIME", "STEAM_COMPAT_", "SSL_",
                  "PRESSURE_VESSEL", "GIO_", "GDK_")
_DENY_SUFFIXES = ("_PATH", "_DIR", "_FILE", "_FILES", "_FILENAMES", "_PREFIX", "_SCRIPT", "_CMD", "_COMMAND",
                  "EXEC")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_ENV_VALUE = re.compile(r"^[A-Za-z0-9_.,:;=+\-@]*$")
SAFE_FLAG = re.compile(r"^[-+]{1,2}[A-Za-z0-9][A-Za-z0-9_.\-=:,+]*$")
SAFE_ARG_VALUE = re.compile(r"^[A-Za-z0-9_.\-=:,+]+$")


# gamescope options that take a value (gamescope --help). Any other word after
# gamescope ends its options, so a stray program name can't ride along.
GAMESCOPE_VALUE_FLAGS = {
    "-W", "-H", "-w", "-h", "-r", "-o", "-F", "-S", "-O", "-s", "-C", "-m", "-R", "-T",
    "--output-width", "--output-height", "--nested-width", "--nested-height", "--nested-refresh",
    "--nested-unfocused-refresh", "--max-scale", "--scaler", "--filter", "--sharpness", "--fsr-sharpness",
    "--prefer-output", "--display-index", "--backend", "--framerate-limit", "--mouse-sensitivity",
    "--cursor-scale-height", "--hdr-itm-target-nits", "--hdr-sdr-content-nits", "--hdr-itm-sdr-nits",
    "--xwayland-count", "--default-touch-mode", "--hide-cursor-delay", "--fade-out-duration",
    "--sdr-gamut-wideness", "--force-orientation", "--prefer-vk-device",
}


def runs_commands(value: str) -> bool:
    """True for values with an ``exec=`` entry, like MangoHud's, which runs a shell command."""
    return any(part.split("=", 1)[0].strip().lower() == "exec" for part in re.split(r"[,:;]", value))


def env_allowed(key: str) -> bool:
    if not ENV_NAME.match(key) or key in _DENY_ENV:
        return False
    if key.startswith(_DENY_PREFIXES) or key.upper().endswith(_DENY_SUFFIXES):
        return False
    return True
