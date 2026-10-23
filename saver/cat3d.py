#!/usr/bin/env python3
"""3D ASCII cat: Omarchy screensaver.

What the cat does depends on the time of day: a cookie in the morning, ramen
at lunch, milk in the evening, and from 21:00 to 05:00 it sleeps.
The cat is a signed-distance-field scene ray-marched into terminal cells.
Frames are rendered once per terminal size and scene and cached, so playback
is cheap. Colours: white and red only. While on AC a charge bar is drawn at
the bottom. CAT_MODE=cookie|ramen|milk|sleep forces a scene.
"""

import fcntl
import gzip
import hashlib
import json
import math
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time

FPS = 12
LOOP = 9.0
NFRAMES = int(FPS * LOOP)
RAMP = " .:;!+*o#%@"
CACHE_DIR = os.path.expanduser("~/.cache/cat-screensaver")
SCRIPT_HASH = hashlib.sha1(open(__file__, "rb").read()).hexdigest()[:10]

WHITE_DIM, WHITE, RED_DIM, RED, RESET = "\033[37m", "\033[97m", "\033[31m", "\033[91m", "\033[0m"


# ---------------------------------------------------------------- animation


def smooth(a, b, t):
    """0 before a, 1 after b, smoothstep in between."""
    if t <= a:
        return 0.0
    if t >= b:
        return 1.0
    x = (t - a) / (b - a)
    return x * x * (3 - 2 * x)


def pulse(a, b, t):
    """0 -> 1 -> 0 over [a, b]."""
    if t <= a or t >= b:
        return 0.0
    return math.sin(math.pi * (t - a) / (b - a))


BITES = [2.4, 3.3, 4.2, 5.1]  # the last bite finishes the cookie
COOKIE_LOW, COOKIE_HIGH = (0, -0.72, 0.64), (0, 0.2, 0.72)


def cookie_params(t):
    tau = 2 * math.pi * t / LOOP
    p = {}
    p["yaw"] = 0.55 * math.sin(tau) + 0.12 * math.sin(3 * tau)
    p["bob"] = 0.02 * math.sin(2 * math.pi * t / 1.5)

    # cookie appears, rises to the mouth, gets eaten, paws go down
    lift = smooth(1.0, 1.9, t) - smooth(5.5, 6.2, t)
    p["lift"] = lift
    bites = sum(1 for b in BITES if t >= b)
    p["bites"] = min(bites, 3)
    p["cookie"] = smooth(0.0, 0.5, t) * (1 - smooth(BITES[-1], BITES[-1] + 0.15, t))

    jaw = 0.0
    chew = 0.0
    for b in BITES:
        jaw = max(jaw, pulse(b - 0.3, b + 0.05, t))
        chew = max(chew, 1 - smooth(b + 0.05, b + 0.75, t) if t >= b else 0.0)
    jaw = max(jaw, 0.35 * chew * abs(math.sin((t * 9.0))))
    p["jaw"] = jaw

    # eyes: closed while chewing happily, a blink later on
    eye = 1.0 - 0.85 * smooth(0, 1, chew * 3)
    eye *= 1.0 - pulse(7.2, 7.5, t)
    p["eye"] = max(0.12, eye)

    p["tongue"] = pulse(5.8, 6.7, t)
    p["pitch"] = 0.32 * (1 - lift) * (1 - smooth(5.6, 6.4, t)) - 0.08 * lift + 0.05 * math.sin(tau * 2)
    p["roll"] = 0.22 * pulse(6.6, 8.6, t)
    p["head_yaw"] = 0.25 * pulse(6.4, 8.8, t) * math.sin(2 * math.pi * (t - 6.4) / 2.4)
    p["ear"] = pulse(8.0, 8.3, t) + pulse(3.6, 3.85, t) * 0.6
    p["tail"] = t

    # crumbs fall from each bite
    crumbs = []
    for i, b in enumerate(BITES):
        age = t - b
        if 0 <= age < 0.9:
            for k in range(3):
                ang = 1.7 * k + i
                vx, vz = 0.25 * math.cos(ang), 0.15 + 0.1 * math.sin(ang)
                crumbs.append((vx * age, -2.4 * age * age + 0.25 * age, vz * age))
    p["crumbs"] = crumbs
    return p


PIVOT = (0.0, 0.32, 0.05)  # neck pivot of the sitting cat
MOUTH = (0.0, 0.04, 0.32)  # mouth in head space
BOWL = (0.0, -0.8, 0.8)  # ramen bowl centre, sits on the floor
SAUCER = (0.0, -1.04, 0.78)  # milk saucer centre


def rot2(a, b, ang):
    c, s = math.cos(ang), math.sin(ang)
    return c * a - s * b, s * a + c * b


def mix3(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def head_to_cat(h, p):
    """Point in head space -> cat space (inverse of the head transform in scene)."""
    hx, hy, hz = h
    hy, hz = rot2(hy, hz, -p["pitch"])
    hx, hy = rot2(hx, hy, p["roll"])
    hx, hz = rot2(hx, hz, p["head_yaw"])
    pv = p["pivot"]
    return (hx + pv[0], hy + pv[1], hz + pv[2])


def ramen_params(t):
    """Three slurps: dip to the bowl, slurp a noodle, chew with closed eyes."""
    tau = 2 * math.pi * t / LOOP
    p = {"yaw": 0.35 * math.sin(tau), "bob": 0.015 * math.sin(2 * math.pi * t / 1.5), "tail": t, "roll": 0.0}
    p["paws"] = {sx: (sx * 0.24, -0.8, 0.58) for sx in (-1, 1)}
    p["bowl"] = 1.0

    s0 = max([s for s in (0.3, 3.3, 6.3) if s <= t], default=6.3 - LOOP)
    dip = smooth(s0, s0 + 0.5, t) - smooth(s0 + 1.8, s0 + 2.4, t)
    p["pivot"] = mix3(PIVOT, (0.0, -0.2, 0.35), dip)
    p["pitch"] = 0.05 - 0.85 * dip + 0.04 * math.sin(2 * tau)
    p["head_yaw"] = 0.12 * math.sin(tau + 1)
    mouth = head_to_cat(MOUTH, p)

    noodles = []
    if s0 + 0.4 < t < s0 + 1.75:
        s = smooth(s0 + 0.5, s0 + 1.65, t)
        start = (BOWL[0], BOWL[1] + 0.02, BOWL[2] - 0.04)
        low = mix3(start, mouth, s)
        for off in (-0.035, 0.035):
            pts = []
            for i in range(7):
                u = i / 6
                q = mix3(low, mouth, u)
                w = 0.05 * (1 - s) * math.sin(14 * u + 22 * t + off * 40) * math.sin(math.pi * u)
                pts.append((q[0] + off + w, q[1], q[2] + 0.5 * w))
            noodles.append(pts)
    p["noodles"] = noodles

    slurp = pulse(s0 + 0.45, s0 + 1.7, t)
    chew = smooth(s0 + 1.6, s0 + 1.7, t) * (1 - smooth(s0 + 2.6, s0 + 2.9, t))
    p["jaw"] = max(0.25 * slurp, 0.35 * chew * abs(math.sin(9 * t)))
    p["eye"] = max(0.12, (1 - 0.85 * chew) * (1 - pulse(2.85, 3.15, t)))
    p["tongue"] = pulse(8.5, 8.95, t)
    p["ear"] = pulse(5.0, 5.3, t)

    steam = []
    for k in range(4):
        ph = (t / 3.0 + k / 4) % 1
        if ph < 0.85:
            steam.append(((0.13 * math.sin(2.1 * k + 5 * ph), BOWL[1] + 0.1 + 0.85 * ph, BOWL[2] - 0.05 + 0.08 * math.cos(1.3 * k)),
                          0.035 + 0.035 * math.sin(math.pi * ph)))
    p["steam"] = steam
    return p


def milk_params(t):
    """Head down at the saucer lapping milk, then up to lick the lips."""
    tau = 2 * math.pi * t / LOOP
    p = {"yaw": 0.3 * math.sin(tau), "bob": 0.0, "tail": t, "saucer": 1.0}
    dip = smooth(0.0, 0.8, t) - smooth(6.2, 6.9, t)
    p["paws"] = {sx: (sx * 0.2, -0.98, 0.42 + 0.06 * dip) for sx in (-1, 1)}
    p["pivot"] = mix3(PIVOT, (0.0, -0.45, 0.42), dip)
    p["pitch"] = 0.1 - 1.1 * dip + 0.04 * math.sin(2 * tau)
    p["roll"] = 0.12 * pulse(6.8, 8.6, t)

    laps = 0.0
    drops = []
    if 0.9 < t < 6.2:
        ph = ((t - 0.9) / 0.45) % 1
        laps = math.sin(math.pi * ph) ** 2
        n = int((t - 0.9) / 0.45)
        age = (t - 0.9) - n * 0.45
        if age < 0.35 and n % 2 == 0:
            for k in range(2):
                vx = 0.25 * (k - 0.5) + 0.05 * math.sin(n)
                drops.append((SAUCER[0] + vx * age * 2, SAUCER[1] + 0.06 + 1.6 * age - 5 * age * age, SAUCER[2] - 0.1))
    p["drops"] = drops
    p["tongue"] = max(laps, pulse(7.0, 7.6, t), pulse(7.8, 8.3, t))
    p["tongue_len"] = laps
    p["jaw"] = 0.3 * laps
    p["eye"] = max(0.12, 1 - 0.5 * dip - pulse(8.4, 8.7, t))
    p["ear"] = pulse(3.5, 3.8, t)
    p["bob"] = 0.01 * laps
    return p


def sleep_params(t):
    """Curled up on the floor, breathing, with Zs floating up."""
    tau = 2 * math.pi * t / LOOP
    breath = math.sin(2 * math.pi * t / 3.0)
    p = {"lie": 1.0, "breath": breath, "tail": t, "eye": 0.0}
    p["yaw"] = 0.22 * math.sin(tau)
    p["pivot"] = (-0.48, -0.86 + 0.012 * breath, 0.2)
    p["pitch"] = 0.18
    p["roll"] = 0.35
    p["head_yaw"] = 0.25
    p["ear"] = pulse(5.4, 5.65, t)
    zs = []
    top = head_to_cat((0.15, 0.6, 0.0), {**p, "pitch": 0.18})
    for k in range(3):
        ph = (t / 3.0 + k / 3) % 1
        if ph < 0.85:
            zs.append(((top[0] + 0.25 + 0.35 * ph + 0.04 * math.sin(6 * ph), top[1] + 0.2 + 0.75 * ph, top[2] + 0.1), 0.05 + 0.09 * ph))
    p["zs"] = zs
    return p


DEFAULTS = {
    "yaw": 0.0, "bob": 0.0, "lift": 0.0, "bites": 0, "cookie": 0.0, "jaw": 0.0, "eye": 1.0, "tongue": 0.0,
    "tongue_len": 0.0, "pitch": 0.0, "roll": 0.0, "head_yaw": 0.0, "ear": 0.0, "tail": 0.0, "crumbs": [],
    "pivot": PIVOT, "paws": None, "bowl": 0.0, "noodles": [], "steam": [], "saucer": 0.0, "drops": [],
    "lie": 0.0, "breath": 0.0, "zs": [],
}
SCENES = {"cookie": cookie_params, "ramen": ramen_params, "milk": milk_params, "sleep": sleep_params}


def scene_params(t, mode="cookie"):
    p = dict(DEFAULTS)
    p.update(SCENES[mode](t))
    return p


def mode_now():
    """cookie 05-11, ramen 11-17, milk 17-21, sleep 21-05."""
    forced = os.environ.get("CAT_MODE")
    if forced in SCENES:
        return forced
    h = time.localtime().tm_hour
    if 5 <= h < 11:
        return "cookie"
    if 11 <= h < 17:
        return "ramen"
    if 17 <= h < 21:
        return "milk"
    return "sleep"


# ---------------------------------------------------------------- SDF scene


def build_sdf(np):
    def length(x, y, z):
        return np.sqrt(x * x + y * y + z * z)

    def ell(x, y, z, c, r):
        qx, qy, qz = (x - c[0]) / r[0], (y - c[1]) / r[1], (z - c[2]) / r[2]
        k0 = length(qx, qy, qz)
        k1 = length(qx / r[0], qy / r[1], qz / r[2]) + 1e-9
        return k0 * (k0 - 1.0) / k1

    def sph(x, y, z, c, r):
        return length(x - c[0], y - c[1], z - c[2]) - r

    def cap(x, y, z, a, b, r1, r2):
        bx, by, bz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        px, py, pz = x - a[0], y - a[1], z - a[2]
        h = np.clip((px * bx + py * by + pz * bz) / (bx * bx + by * by + bz * bz + 1e-12), 0.0, 1.0)
        return length(px - bx * h, py - by * h, pz - bz * h) - (r1 + (r2 - r1) * h)

    def smin(a, b, k):
        h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
        return b * (1 - h) + a * h - k * h * (1 - h)

    def smax(a, b, k):
        return -smin(-a, -b, k)

    def rot(a, b, ang):
        c, s = math.cos(ang), math.sin(ang)
        return c * a - s * b, s * a + c * b

    def mix3(a, b, t):
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))

    def scene(x, y, z, P):
        """Returns (white distance, red distance)."""
        # world -> cat space
        x, z = rot(x, z, -P["yaw"])
        y = y - P["bob"]
        lift = P["lift"]

        t = P["tail"]
        pv = P["pivot"]
        if P["lie"]:
            # lying on its side along x, breathing, head resting on the front paws
            b = 1 + 0.035 * P["breath"]
            d = ell(x, y, z, (0.12, -0.8, -0.05), (0.72, 0.27 * b, 0.42 * b))
            d = smin(d, ell(x, y, z, (0.5, -0.76, -0.08), (0.36, 0.31 * b, 0.4 * b)), 0.2)
            d = smin(d, ell(x, y, z, (-0.3, -0.8, 0.04), (0.34, 0.26 * b, 0.36 * b)), 0.2)
            d = smin(d, cap(x, y, z, (-0.3, -0.92, 0.22), (-0.6, -1.0, 0.46), 0.085, 0.075), 0.06)
            d = smin(d, cap(x, y, z, (-0.1, -0.95, 0.26), (-0.32, -1.0, 0.52), 0.085, 0.075), 0.06)
            d = smin(d, ell(x, y, z, (0.62, -0.99, 0.3), (0.2, 0.07, 0.12)), 0.08)
            d = smin(d, cap(x, y, z, (-0.3, -0.72, 0.05), pv, 0.2, 0.16), 0.1)
            # tail wrapped around the front, the tip twitching
            prev = None
            for i in range(9):
                s = i / 8
                ang = -0.3 + s * 2.3
                sway = 0.06 * s * s * math.sin(2 * math.pi * t / 4.5)
                pt = (0.15 + 0.72 * math.cos(ang) + sway, -0.99 + 0.02 * s, 0.02 + 0.55 * math.sin(ang))
                if prev is not None:
                    d = smin(d, cap(x, y, z, prev, pt, 0.085 - 0.03 * (i - 1) / 8, 0.085 - 0.03 * i / 8), 0.05)
                prev = pt
        else:
            # body: haunches + chest
            d = ell(x, y, z, (0, -0.66, -0.08), (0.62, 0.42, 0.55))
            d = smin(d, ell(x, y, z, (0, -0.16, 0.06), (0.37, 0.46, 0.33)), 0.25)
            for sx in (-1, 1):
                d = smin(d, ell(x, y, z, (sx * 0.36, -0.76, 0.1), (0.21, 0.22, 0.36)), 0.1)
                d = smin(d, ell(x, y, z, (sx * 0.3, -1.0, 0.42), (0.11, 0.065, 0.15)), 0.06)
            if pv != PIVOT:  # neck stretched down toward a bowl
                d = smin(d, cap(x, y, z, (0, 0.0, 0.08), pv, 0.2, 0.17), 0.1)

            # front legs: holding the cookie, or on the bowl / floor
            for sx in (-1, 1):
                shoulder = (sx * 0.19, -0.05, 0.2)
                if P["paws"]:
                    paw = P["paws"][sx]
                    elbow = mix3(shoulder, paw, 0.5)
                    elbow = (elbow[0] + sx * 0.05, elbow[1], elbow[2])
                else:
                    low = (sx * 0.17, -0.82, 0.44)
                    high = (sx * 0.22, COOKIE_HIGH[1] - 0.02, COOKIE_HIGH[2] - 0.08)
                    paw = mix3(low, high, lift)
                    elbow = mix3(mix3(shoulder, paw, 0.5), (sx * 0.27, -0.25 + 0.1 * lift, 0.33), 0.5 * lift)
                d = smin(d, cap(x, y, z, shoulder, elbow, 0.1, 0.09), 0.08)
                d = smin(d, cap(x, y, z, elbow, paw, 0.09, 0.08), 0.05)
                d = smin(d, ell(x, y, z, paw, (0.09, 0.075, 0.1)), 0.04)

            # tail curling around the side, swaying
            prev = None
            for i in range(9):
                s = i / 8
                ang = -math.pi / 2 + s * 2.2
                sway = 0.25 * s * s * math.sin(2 * math.pi * t / 3.0 - s * 2.5)
                rr = 0.62 + 0.1 * s
                pt = (rr * math.sin(ang) + sway, -0.98 + 0.5 * s ** 2.5, -rr * math.cos(ang) * 0.8)
                if prev is not None:
                    d = smin(d, cap(x, y, z, prev, pt, 0.085 - 0.03 * (i - 1) / 8, 0.085 - 0.03 * i / 8), 0.05)
                prev = pt

        # head space: pivot at neck, pitch/roll/yaw
        hx, hy, hz = x - pv[0], y - pv[1], z - pv[2]
        hx, hz = rot(hx, hz, -P["head_yaw"])
        hx, hy = rot(hx, hy, -P["roll"])
        hy, hz = rot(hy, hz, P["pitch"])

        head = ell(hx, hy, hz, (0, 0.26, 0.06), (0.47, 0.39, 0.4))
        for sx in (-1, 1):
            head = smin(head, ell(hx, hy, hz, (sx * 0.13, 0.1, 0.3), (0.14, 0.1, 0.12)), 0.06)
        jaw = P["jaw"]
        head = smin(head, ell(hx, hy, hz, (0, 0.03 - 0.07 * jaw, 0.27 - 0.02 * jaw), (0.1, 0.055, 0.09)), 0.03)

        red = 1e9 + 0 * x
        ear_tw = P["ear"]
        for sx in (-1, 1):
            base = (sx * 0.22, 0.48, 0.02)
            tip = (sx * (0.36 + 0.06 * ear_tw * (sx > 0)), 0.86 - 0.08 * ear_tw * (sx > 0), 0.0)
            # flatten ears front-to-back
            ear = cap(hx, hy, hz * 1.9, (base[0], base[1], base[2] * 1.9), (tip[0], tip[1], 0), 0.15, 0.015) / 1.9
            head = smin(head, ear, 0.04)
            inner = cap(hx, hy, (hz - 0.035) * 2.6, (base[0] * 0.95, base[1] + 0.03, 0.05), (tip[0] * 0.92, tip[1] - 0.07, 0.0), 0.09, 0.01) / 2.6
            red = np.minimum(red, inner)
            # eyes
            red = np.minimum(red, ell(hx, hy, hz, (sx * 0.16, 0.29, 0.385), (0.085, 0.1 * P["eye"] + 0.006, 0.05)))

        red = np.minimum(red, ell(hx, hy, hz, (0, 0.17, 0.44), (0.04, 0.028, 0.035)))  # nose
        if jaw > 0.2:  # open mouth
            red = np.minimum(red, ell(hx, hy, hz, (0, 0.07 - 0.03 * jaw, 0.33), (0.06, 0.03 * jaw, 0.04)))
        if P["tongue"] > 0:
            tg, tl = P["tongue"], P["tongue_len"]
            red = np.minimum(red, ell(hx, hy, hz, (0, 0.05 - 0.05 * tl, 0.3 + 0.1 * tg + 0.1 * tl),
                                     (0.045, 0.02, 0.06 * tg + 0.01 + 0.07 * tl)))

        d = smin(d, head, 0.14)

        if P["bowl"]:
            # red ramen bowl with white noodles, an egg and chopsticks
            bx, by, bz = BOWL
            bowl = np.maximum(sph(x, y, z, BOWL, 0.27), y - (by + 0.06))
            bowl = np.maximum(bowl, -sph(x, y, z, BOWL, 0.235))
            bowl = np.maximum(bowl, -1.07 - y)
            red = np.minimum(red, bowl)
            soup = np.maximum(sph(x, y, z, BOWL, 0.24), y - (by + 0.01))
            for nx_, nz_ in ((-0.08, 0.05), (0.06, 0.08), (0.0, -0.08), (-0.1, -0.06)):
                soup = smin(soup, ell(x, y, z, (bx + nx_, by + 0.02, bz + nz_), (0.08, 0.03, 0.06)), 0.03)
            egg = ell(x, y, z, (bx + 0.1, by + 0.03, bz + 0.06), (0.07, 0.035, 0.06))
            d = np.minimum(d, np.minimum(soup, egg))
            red = np.minimum(red, sph(x, y, z, (bx + 0.1, by + 0.06, bz + 0.06), 0.032))  # yolk
            for cx_ in (0.1, 0.16):
                red = np.minimum(red, cap(x, y, z, (bx + cx_ - 0.05, by, bz - 0.1), (bx + cx_ + 0.12, by + 0.5, bz - 0.22), 0.022, 0.018))
            for strand in P["noodles"]:
                for a_, b_ in zip(strand, strand[1:]):
                    d = np.minimum(d, cap(x, y, z, a_, b_, 0.032, 0.032))
            for c_, r_ in P["steam"]:
                d = np.minimum(d, sph(x, y, z, c_, r_))

        if P["saucer"]:
            # flat red saucer with a white pool of milk
            sx_, sy_, sz_ = SAUCER
            q = np.sqrt((x - sx_) ** 2 + (z - sz_) ** 2)
            dish = np.maximum(q - 0.36, np.abs(y - sy_) - 0.03)
            rim = np.sqrt((q - 0.34) ** 2 + (y - sy_ - 0.03) ** 2) - 0.028
            red = np.minimum(red, np.minimum(dish, rim))
            d = np.minimum(d, np.maximum(q - 0.3, np.abs(y - sy_ - 0.035) - 0.012))
            for c_ in P["drops"]:
                d = np.minimum(d, sph(x, y, z, c_, 0.03))

        for (zx, zy, zz), s in P["zs"]:
            zr = 0.022 + 0.08 * s
            zd = cap(x, y, z, (zx - s, zy + s, zz), (zx + s, zy + s, zz), zr, zr)
            zd = np.minimum(zd, cap(x, y, z, (zx + s, zy + s, zz), (zx - s, zy - s, zz), zr, zr))
            zd = np.minimum(zd, cap(x, y, z, (zx - s, zy - s, zz), (zx + s, zy - s, zz), zr, zr))
            red = np.minimum(red, zd)

        # cookie between the paws: a tilted disk with chocolate dimples and bites
        cs = P["cookie"]
        if cs > 0.01:
            c = mix3(COOKIE_LOW, COOKIE_HIGH, lift)
            tilt = 0.55 + 0.2 * lift
            qx, qy, qz = (x - c[0]) / cs, (y - c[1]) / cs, (z - c[2]) / cs
            cc, ss = math.cos(tilt), math.sin(tilt)
            qy, qz = cc * qy + ss * qz, -ss * qy + cc * qz
            R, H = 0.22, 0.04
            dr = np.sqrt(qx * qx + qz * qz) - R
            dh = np.abs(qy) - H
            ck = np.minimum(np.maximum(dr, dh), 0) + length(np.maximum(dr, 0), np.maximum(dh, 0), 0 * dr) - 0.012
            for cx, cz in ((0.08, 0.05), (-0.09, 0.02), (0.0, -0.1), (-0.04, 0.12), (0.12, -0.08)):
                ck = smax(ck, -sph(qx, qy, qz, (cx, H + 0.02, cz), 0.035), 0.01)
            for i, ang in enumerate((-90, -55, -125)[: P["bites"]]):
                a = math.radians(ang)
                ck = smax(ck, -sph(qx, qy, qz, (R * math.cos(a), 0, R * math.sin(a)), 0.11), 0.015)
            red = np.minimum(red, ck * cs)

        for cr in P["crumbs"]:
            base = mix3(COOKIE_LOW, COOKIE_HIGH, lift)
            red = np.minimum(red, sph(x, y, z, (base[0] + cr[0], base[1] + 0.12 + cr[1], base[2] + cr[2]), 0.035))

        return d, red

    return scene


# ---------------------------------------------------------------- renderer


def render_frame(np, scene, P, cols, rows, aspect):
    """Ray-march one frame. Returns a list of `rows` coloured strings."""
    ro = np.array([0.0, 0.25, 4.3])
    target = np.array([0.0, 0.02, 0.0])
    fwd = target - ro
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 1, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)

    tan_half = 0.345
    j, i = np.meshgrid(np.arange(cols), np.arange(rows))
    v = -(i - rows / 2 + 0.5) / (rows / 2) * tan_half
    u = (j - cols / 2 + 0.5) / (rows / 2) * tan_half / aspect
    u, v = u.ravel(), v.ravel()
    dx = fwd[0] + u * right[0] + v * up[0]
    dy = fwd[1] + u * right[1] + v * up[1]
    dz = fwd[2] + u * right[2] + v * up[2]
    n = np.sqrt(dx * dx + dy * dy + dz * dz)
    dx, dy, dz = dx / n, dy / n, dz / n
    N = dx.size

    def dist(x, y, z):
        a, b = scene(x, y, z, P)
        return np.minimum(a, b)

    # bounding sphere around the cat
    bc, br = np.array([0.0, -0.1, 0.1]), 1.55
    ox, oy, oz = ro - bc
    b = ox * dx + oy * dy + oz * dz
    disc = b * b - (ox * ox + oy * oy + oz * oz - br * br)
    tq = np.where(disc > 0, -b - np.sqrt(np.maximum(disc, 0)), 1e9)

    t = np.maximum(tq, 0.0)
    hit = np.zeros(N, bool)
    active = np.nonzero(disc > 0)[0]
    tmax = np.where(disc > 0, -b + np.sqrt(np.maximum(disc, 0)), 0)
    for _ in range(90):
        if active.size == 0:
            break
        ta = t[active]
        d = dist(ro[0] + dx[active] * ta, ro[1] + dy[active] * ta, ro[2] + dz[active] * ta)
        done = d < 0.002 * ta
        hit[active[done]] = True
        t[active] = ta + d * 0.85
        keep = ~done & (t[active] < tmax[active])
        active = active[keep]

    lum = np.zeros(N)
    red = np.zeros(N, bool)
    L = np.array([-0.45, 0.65, 0.75])
    F = np.array([0.6, -0.25, 0.8])  # fill light from the lower right
    L /= np.linalg.norm(L)

    idx = np.nonzero(hit)[0]
    if idx.size:
        th = t[idx]
        px, py, pz = ro[0] + dx[idx] * th, ro[1] + dy[idx] * th, ro[2] + dz[idx] * th
        e = 0.004
        k = ((1, -1, -1), (-1, -1, 1), (-1, 1, -1), (1, 1, 1))
        nx = ny = nz = 0
        for kx, ky, kz in k:
            dd = dist(px + kx * e, py + ky * e, pz + kz * e)
            nx, ny, nz = nx + kx * dd, ny + ky * dd, nz + kz * dd
        nl = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
        nx, ny, nz = nx / nl, ny / nl, nz / nl

        wd, rd = scene(px, py, pz, P)
        red[idx] = rd < wd

        diff = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
        ao = np.clip(dist(px + nx * 0.12, py + ny * 0.12, pz + nz * 0.12) / 0.12, 0.25, 1)
        hx, hy, hz = L[0] - dx[idx], L[1] - dy[idx], L[2] - dz[idx]
        hl = np.sqrt(hx * hx + hy * hy + hz * hz)
        spec = np.clip((nx * hx + ny * hy + nz * hz) / hl, 0, 1) ** 30
        rim = (1 - np.clip(-(nx * dx[idx] + ny * dy[idx] + nz * dz[idx]), 0, 1)) ** 3

        # soft shadow toward the light
        sh = np.ones(idx.size)
        st = np.full(idx.size, 0.03)
        for _ in range(14):
            dd = dist(px + nx * 0.01 + L[0] * st, py + ny * 0.01 + L[1] * st, pz + nz * 0.01 + L[2] * st)
            sh = np.minimum(sh, np.clip(8 * dd / st, 0, 1))
            st += np.clip(dd, 0.02, 0.2)
        sh = 0.5 + 0.5 * sh

        fill = np.clip(nx * F[0] + ny * F[1] + nz * F[2], 0, 1) / np.linalg.norm(F)
        lit = 0.08 + 0.1 * (0.5 + 0.5 * ny) + 0.65 * diff * sh * ao + 0.2 * fill * ao + 0.35 * spec * sh + 0.25 * rim
        lum[idx] = np.clip(lit ** 1.35, 0.1, 1.0)

    # floor with the cat's shadow
    floor_y = -1.07
    miss = np.nonzero(~hit & (dy < 0))[0]
    if miss.size:
        tf = (floor_y - ro[1]) / dy[miss]
        fx, fz = ro[0] + dx[miss] * tf, ro[2] + dz[miss] * tf
        r = np.sqrt(fx * fx + (fz - 0.1) ** 2 * 1.4)
        near = r < 1.45
        m, fx, fz, r = miss[near], fx[near], fz[near], r[near]
        if m.size:
            fy = np.full(m.size, floor_y)
            sh = np.ones(m.size)
            st = np.full(m.size, 0.04)
            for _ in range(16):
                dd = dist(fx + L[0] * st, fy + L[1] * st, fz + L[2] * st)
                sh = np.minimum(sh, np.clip(6 * dd / st, 0, 1))
                st += np.clip(dd, 0.03, 0.25)
            fade = np.clip(1 - r / 1.45, 0, 1) ** 0.6
            lum[m] = 0.21 * fade * (0.1 + 0.9 * sh)

    lum = lum.reshape(rows, cols)
    red = red.reshape(rows, cols)
    hitg = hit.reshape(rows, cols)
    chars = np.array(list(RAMP))
    ci = np.clip((lum * (len(RAMP) - 0.01)).astype(int), 0, len(RAMP) - 1)
    ci = np.where(hitg & (ci < 1), 1, ci)

    lines = []
    for r_ in range(rows):
        out, cur = [], None
        row_chars = chars[ci[r_]]
        for c_ in range(cols):
            ch = row_chars[c_]
            if ch == " ":
                out.append(ch)
                continue
            bright = lum[r_, c_] > 0.5
            col = (RED if bright else RED_DIM) if red[r_, c_] else (WHITE if bright else WHITE_DIM)
            if col != cur:
                out.append(col)
                cur = col
            out.append(ch)
        lines.append("".join(out).rstrip() + RESET)
    return lines


class Frames:
    """Per-size frame cache; renders missing frames on demand."""

    def __init__(self, cols, rows, aspect, mode="cookie"):
        self.cols, self.rows, self.aspect, self.mode = cols, rows, aspect, mode
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.path = os.path.join(CACHE_DIR, f"{mode}-{cols}x{rows}-{aspect:.2f}-{SCRIPT_HASH}.json.gz")
        self.frames = [None] * NFRAMES
        self.saved = False
        self.load()
        self._np = self._scene = None
        self._bg = None

    def load(self):
        try:
            with gzip.open(self.path, "rt") as f:
                data = json.load(f)
            if len(data) == NFRAMES:
                self.frames = data
                self.saved = True
        except (OSError, ValueError, EOFError):
            pass
        return self.saved

    def get_playing(self, k):
        """For the player: while the cache is missing, render it in the
        background (all cores, low priority) and show a still frame.
        The other scenes are rendered right after, ready for later in the day."""
        if self.saved:
            return self.frames[k]
        if self._bg is None:
            self._bg = subprocess.Popen(
                ["nice", "-n", "15", sys.executable, os.path.abspath(__file__), "--prerender-exact",
                 str(self.cols), str(self.rows), repr(self.aspect), self.mode,
                 *[m for m in SCENES if m != self.mode]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self._bg.poll() is not None and self.load():
            return self.frames[k]
        return self.get(0)

    def get(self, k):
        if self.frames[k] is None:
            if self._np is None:
                import numpy

                self._np = numpy
                self._scene = build_sdf(numpy)
            self.frames[k] = render_frame(self._np, self._scene, scene_params(k / FPS, self.mode), self.cols, self.rows, self.aspect)
            if not self.saved and all(f is not None for f in self.frames):
                tmp = self.path + ".tmp"
                with gzip.open(tmp, "wt") as f:
                    json.dump(self.frames, f)
                os.replace(tmp, self.path)
                self.saved = True
                for name in os.listdir(CACHE_DIR):  # drop caches of older versions
                    if SCRIPT_HASH not in name:
                        os.remove(os.path.join(CACHE_DIR, name))
        return self.frames[k]


# ---------------------------------------------------------------- power


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def find_supplies():
    ac = bat = None
    base = "/sys/class/power_supply"
    for name in sorted(os.listdir(base)):
        typ = read(f"{base}/{name}/type")
        if typ == "Mains" and ac is None:
            ac = f"{base}/{name}"
        elif typ == "Battery" and bat is None:
            bat = f"{base}/{name}"
    return ac, bat


def charge_line(bat, width):
    cap = int(read(f"{bat}/capacity", "0") or 0)
    status = read(f"{bat}/status")
    now = int(read(f"{bat}/charge_now", read(f"{bat}/energy_now", "0")) or 0)
    full = int(read(f"{bat}/charge_full", read(f"{bat}/energy_full", "0")) or 0)
    rate = int(read(f"{bat}/current_now", read(f"{bat}/power_now", "0")) or 0)

    if status == "Charging":
        label = "заряжается"
    elif status == "Full":
        label = "заряжено"
    else:
        label = "подключено"
    left = ""
    if status == "Charging" and rate > 0 and full > now:
        mins = (full - now) * 60 // rate
        left = f"   ~{mins // 60}:{mins % 60:02d} до полной"

    n = 30
    filled = cap * n // 100
    head = f"{label}  {cap:3d}%   "
    bar = RED + "━" * filled + WHITE_DIM + "─" * (n - filled)
    plain_len = len(head) + n + len(left)
    pad = max(0, (width - plain_len) // 2)
    return " " * pad + WHITE + head + bar + WHITE + left + RESET


# ---------------------------------------------------------------- player


def term_size():
    try:
        rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
    except OSError:
        return 80, 24, 2.0
    aspect = (yp / rows) / (xp / cols) if xp and yp else 2.0
    return cols, rows, aspect


def hypr(*args):
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def set_cursor_invisible(on):
    val = "true" if on else "false"
    if "ok" not in hypr("eval", f"hl.config({{ cursor = {{ invisible = {val} }} }})"):
        hypr("keyword", "cursor:invisible", val)


def in_focus():
    try:
        return json.loads(hypr("activewindow", "-j") or "{}").get("class") == "org.omarchy.screensaver"
    except ValueError:
        return True


def lid_open():
    import glob

    return not any("closed" in read(p) for p in glob.glob("/proc/acpi/button/lid/*/state"))


def config_lock_after():
    battery_lock = 300
    for line in read(os.path.expanduser("~/.config/omarchy/cat-screensaver.conf")).splitlines():
        if line.strip().startswith("BATTERY_LOCK="):
            try:
                battery_lock = int(line.split("=", 1)[1])
            except ValueError:
                pass
    try:
        with open(os.path.expanduser("~/.config/omarchy/shell.json")) as f:
            saver = int(json.load(f).get("idle", {}).get("screensaver", 150))
    except (OSError, ValueError):
        saver = 150
    return battery_lock - saver


def play():
    out = sys.stdout
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    state = {"resized": True}

    def finish(*_):
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\033[0m\033[?25h")
        out.flush()
        set_cursor_invisible(False)
        subprocess.run(["pkill", "-f", "[o]rg.omarchy.screensaver"])
        sys.exit(0)

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(s, finish)
    signal.signal(signal.SIGWINCH, lambda *_: state.update(resized=True))

    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ICANON | termios.ECHO)
    termios.tcsetattr(fd, termios.TCSADRAIN, new)

    out.write("\033]11;rgb:00/00/00\007\033[?25l\033[2J")
    out.flush()
    set_cursor_invisible(True)

    # terminals start at 80x24 until the compositor sizes the window
    deadline = time.time() + 2
    while time.time() < deadline and term_size()[:2] == (80, 24):
        time.sleep(0.02)

    ac, bat = find_supplies()
    lock_after = config_lock_after()
    start = time.time()
    ac_seen = False
    last_check = 0
    status = ""
    frames = None
    k = 0
    next_t = time.time()

    while True:
        now = time.time()
        if now - last_check >= 1:
            last_check = now
            if not in_focus():
                finish()
            on_ac = ac is not None and read(f"{ac}/online") == "1"
            if on_ac:
                ac_seen = True
            elif ac_seen and lock_after > 0 and now - start >= lock_after + 5:
                # idle lock is off on AC; charger was pulled while we were up
                subprocess.Popen(["omarchy-system-lock"])
                finish()
            mode = mode_now()
            if state["resized"] or frames.mode != mode:
                state["resized"] = False
                cols, rows, aspect = term_size()
                frames = Frames(cols, max(rows - 2, 4), aspect, mode)
                k = 0
                out.write("\033[2J")
            status = charge_line(bat, cols) if on_ac and bat else ""
            awake = lid_open()

        if awake:
            lines = frames.get_playing(k)
            buf = ["\033[H"]
            for r, line in enumerate(lines):
                buf.append(f"\033[{r + 1};1H\033[2K{line}")
            buf.append(f"\033[{rows};1H\033[2K{status}")
            out.write("".join(buf))
            out.flush()
            k = (k + 1) % NFRAMES

        next_t = max(next_t + 1 / FPS, time.time())
        timeout = next_t - time.time() if awake else 1.0
        r, _, _ = select.select([fd], [], [], max(0, timeout))
        if r:
            finish()


def _render_one(args):
    import numpy

    k, cols, rows, aspect, mode = args
    return render_frame(numpy, build_sdf(numpy), scene_params(k / FPS, mode), cols, rows, aspect)


def prerender(cols, rows, aspect, modes):
    from multiprocessing import Pool

    for mode in modes:
        f = Frames(cols, rows, aspect, mode)
        if f.saved:
            continue
        t0 = time.time()
        with Pool(os.cpu_count()) as pool:
            for k, lines in enumerate(pool.map(_render_one, [(k, cols, rows, aspect, mode) for k in range(NFRAMES)])):
                f.frames[k] = lines
        f.frames[-1] = None
        f.get(NFRAMES - 1)  # renders the last one and writes the cache
        print(f"rendered {mode}: {NFRAMES} frames {cols}x{rows} in {time.time() - t0:.1f}s -> {f.path}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--prerender-exact":
        prerender(int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), sys.argv[5:] or list(SCENES))
    elif len(sys.argv) >= 2 and sys.argv[1] == "--frame":
        import numpy

        cols, rows, aspect, t = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
        mode = sys.argv[6] if len(sys.argv) > 6 else mode_now()
        for line in render_frame(numpy, build_sdf(numpy), scene_params(t, mode), cols, rows, aspect):
            print(line)
    else:
        play()
