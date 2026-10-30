# DragSnap

**Fenster beim Ziehen an den Bildschirmrand andocken – für Openbox und andere
X11-Fenstermanager, die das nicht selbst können.**

[English version](README.md)

Ein Fenster an der Titelleiste ziehen und den Bildschirmrand berühren:

- **linker oder rechter Rand:** das Fenster füllt diese Hälfte des Monitors
- **oberer Rand:** das Fenster wird maximiert

Das passiert, sobald der Mauszeiger den Rand berührt – loslassen ist nicht
nötig.

- Klappt auch mit maximierten Fenstern: an den linken Rand gezogen, werden sie
  zur linken Hälfte.
- Bei mehreren Monitoren zählen nur die äußeren Ränder. Die Kante zwischen zwei
  Monitoren bleibt frei, Fenster lassen sich also weiter hinüberziehen.
- Panels und Taskleisten werden berücksichtigt, das Fenster füllt genau den
  freien Bereich.
- Größe ändern am Fensterrand, Text markieren oder Dateien ziehen löst nichts
  aus.

## Voraussetzungen

- eine X11-Sitzung (unter Wayland macht das der Compositor selbst)
- ein EWMH-fähiger Fenstermanager ohne eigenes Kanten-Andocken – geschrieben
  vor allem für **Openbox** (LXQt, Lubuntu, LXDE), sollte auch z. B. mit
  Fluxbox, IceWM oder i3 im Floating-Modus gehen
- Python 3, `xrandr` sowie libXi, libXfixes, libXtst

Unter Debian/Ubuntu:

```sh
sudo apt install python3 x11-xserver-utils libxi6 libxfixes3 libxtst6
```

Getestet unter Ubuntu 22.04 mit LXQt und Openbox 3.6, zwei Monitore.

## Installation

```sh
git clone https://github.com/Codatus/dragsnap.git
cd dragsnap
./install.sh
```

Ohne git:

```sh
wget -qO- https://github.com/Codatus/dragsnap/archive/refs/heads/main.tar.gz | tar xz && cd dragsnap-main && ./install.sh
```

Das Skript installiert für den aktuellen Benutzer:

| Datei | Zweck |
|---|---|
| `~/.local/bin/dragsnap` | das Programm |
| `~/.config/systemd/user/dragsnap.service` | systemd-Benutzerdienst, startet nach Abstürzen neu |
| `~/.config/autostart/dragsnap-start.desktop` | startet den Dienst beim Anmelden |

Der Autostart-Eintrag ist nötig, weil viele schlanke Desktops (LXQt, Xfce, …)
systemds `graphical-session.target` nie aktivieren.

Entfernen:

```sh
./install.sh --uninstall
```

## Funktionsweise

Während ein Fenstermanager ein Fenster verschiebt, hält er den Mauszeiger fest –
andere Programme sehen keine Mausereignisse. Rohe XInput2-Ereignisse kommen
trotzdem an. Solange die linke Taste gedrückt ist, prüft DragSnap, ob gerade ein
Fenster verschoben wird (am Verschiebe-Mauszeiger des Fenstermanagers oder daran,
dass sich der Fensterrahmen als Ganzes bewegt) und ob der Zeiger an einem
äußeren Rand steht. Dann beendet es das Verschieben mit einem künstlichen
Enter – der Taste, mit der der Fenstermanager selbst das Verschieben abschließt –
und schickt die neue Geometrie als gewöhnliche EWMH-Anfrage.

DragSnap fragt nicht ständig ab: Ohne gedrückte Maustaste bekommt es nur
Maustastenereignisse und liest die Zeigerposition nur, solange eine Taste gedrückt
ist.

## Fehlersuche

```sh
systemctl --user status dragsnap
dragsnap -v     # im Vordergrund, mit Protokollausgabe
```

## Einschränkungen

- Das Andocken beendet das Ziehen. Soll das Fenster doch woanders hin, einfach
  neu anfassen.
- Verwendet der Fenstermanager einen anderen Verschiebe-Mauszeiger als
  `fleur`/`move`, werden maximierte Fenster beim Ziehen nicht erkannt (normale
  Fenster schon).
- Fenstermanager, die selbst andocken (Xfwm, KWin, Mutter, Muffin, …),
  brauchen DragSnap nicht – sonst würden beide gleichzeitig handeln.

## Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE).
