# CodeMind — Ideen & Fahrplan (nur Plan, nichts davon ist gebaut)

Status: **Planungsdokument**. Stand `f336ad2`. Hier steht bewusst kein Code und keine Zusage, nur
Ideen, Priorisierung und Reihenfolge. Aufwandseinheiten: **S** = ein Abend, **M** = 2–4 Abende,
**L** = 1–2 Wochen. „Messbar" heißt: es gibt am Ende eine Zahl oder einen Test, der die Behauptung
belegt — das ist die Grundregel dieses Projekts.

---

## 0. Nordstern: wofür CodeMind in einem Satz stehen soll

Drei Kandidaten, die sich gegenseitig ausschließen, wenn man sie alle gleichzeitig groß machen will:

| Kandidat | Kernversprechen | Passt zum Bestand |
| --- | --- | --- |
| **A · Beweisbar ehrlich** | „Kein Erfolg ohne Beleg" — jede Aufgabe liefert nachprüfbare Evidenz, und die Zahl der unbelegten Behauptungen ist 0. | Sehr gut: Verifikation, Evidence-Checks, Reparatur-Budget, Ehrlichkeits-Tests existieren schon |
| **B · Projektgedächtnis** | „Kennt dein Repo besser als ein neuer Kollege in Woche 1." | Gut: Memory + Knowledge-Index vorhanden, aber noch generisch |
| **C · Persönlicher CI** | „Schläft nicht: findet, plant und liefert Wartung nachts." | Mittel: Autonomie-Scheduler und Vorschläge existieren, der Kreislauf fehlt |

**Empfehlung:** A als Identität (Hauptprägung), B als Tiefe (macht A schwer kopierbar), C als
Endausbau (macht A im Alltag spürbar). Alles andere ordnet sich darunter.

---

## 1. Signatur-Features (Kategorie A) — das, was CodeMind unverwechselbar macht

### A1 · Change Passport (Beweisakte pro Aufgabe) — **M** — Impact 5
- **Was:** Jede abgeschlossene Aufgabe exportiert eine kompakte Akte: Ziel, Plan, Diff mit Zeilen,
  ausgeführte Befehle mit Exit-Codes, Testergebnis, Verifikations-Checks, benutzte Modelle/Adapter,
  Dauer, sowie eine Prüfsummen-Kette über alle Einträge (Hash-Verkettung im Log).
- **Prägend:** Man kann fremden (oder dem eigenen) Agenten nicht mehr glauben oder nicht glauben —
  man prüft. Als Markdown/HTML/JSON exportierbar, optional als PR-Beschreibung.
- **Messbar:** „Honesty-Rate" = Anteil abgeschlossener Aufgaben mit vollständiger, hash-valider Akte
  → Ziel 100 %; ein Test bricht den Hash und prüft, dass die Validierung Alarm gibt.
- **Abhängig von:** vorhandener Verifikation, `task_steps.result`, `tool_calls`, `FileJournal`.

### A2 · Replay & Counterfactual (Zeitreise für Aufgaben) — **L** — Impact 5
- **Was:** Aufgezeichnete Aufgaben deterministisch in frischem Sandbox-Umfeld nachspielen
  (gleiche Schritte, gleiche Eingaben) und optional „Was wäre, wenn Tool X gewählt worden wäre?"
  rechnen. Dazu Drift-Meldung, wenn ein Replay abweicht.
- **Prägend:** Der Agent kann seine eigene Vergangenheit erklären *und reproduzieren* — Grundlage für
  Vertrauen, Regression-Jagd und das Erzeugen echter Trainingsdaten.
- **Messbar:** 20 historische Aufgaben replayen: Anteil identischer Endzustände (Ziel ≥ 90 %), und
  jedes Replay erzeugt ein Beispiel mit Qualitätsurteil.

### A3 · CodeMind Bench + Honesty-Rate (öffentliche Messlatte) — **M** — Impact 4
- **Was:** Reproduzierbare lokale Benchmark-Suite: Tool-Auswahl, Reparatur-Erfolgsquote nach
  Fehlersignatur, Verifikationstreue (nie Erfolg ohne Beleg), Zeit/MB pro Aufgabe, Verhalten ohne
  Modell vs. mit Modell. Ergebnisse je Hardware-Tier als Tabelle im README.
- **Prägend:** Ehrliche Zahlen statt Marketing — genau die Haltung, die das Projekt schon im Code hat.
- **Messbar:** Ein Befehl (`main.py --bench`) erzeugt `bench/results/<tier>.json`, und ein Test
  verhindert Regressionen über 5 %.

### A4 · Projektgedächtnis mit Konventionen und Verfall — **L** — Impact 5
- **Was:** Aus dem Code abgeleitete, strukturierte Projektregeln (Namensschema, Teststil,
  Commit-Format, Fehlerbehandlung, Logging, Ordnerlogik) mit Konfidenz und Fundstelle. Edits werden
  dagegen geprüft. Jede Erinnerung bekommt Herkunft (Task, Commit, Datei) und **verfällt**, wenn sich
  der zugehörige Code ändert.
- **Prägend:** „Der Agent kennt *dein* Repo" statt „der Agent kennt Code allgemein".
- **Messbar:** Nach 5 Aufgaben auf demselben Repo: Anteil der Edits ohne Konventionsverstoß steigt
  messbar; veraltete Memories werden von selbst als „stale" markiert (sichtbar im GUI).

### A5 · Persönlicher CI / Nachtmodus — **M** — Impact 4
- **Was:** Watch-Modus (Zeitplan, Git-Hook, manuell): Tests, Lint, veraltete Abhängigkeiten,
  TODO-Triage, langsame/flakige Tests, toter Code → daraus Vorschläge → optional Draft-PR.
- **Prägend:** Kein Chatfenster, sondern ein Hintergrund-Maintainer, der morgens eine Liste vorlegt.
- **Messbar:** 7 Nächte laufen lassen, Vorschlags-Qualität = Anteil angenommener Vorschläge (Ziel > 40 %).

### A6 · Vault-Modus (USB, portabel, verschlüsselt) — **M** — Impact 3
- **Was:** Gesamtzustand (DB, Modelle, Datensätze, Checkpoints, Secrets) auf dem 128-GB-Stick als
  verschlüsselter Vault (LUKS oder SQLCipher + age für Secrets), Boot von beliebigem Rechner,
  Schlüssel per Passphrase/Keyfile, klare „Vault gesperrt/entsperrt"-Zustände im GUI.
- **Prägend:** „Deine KI, dein Stick, kein Cloudkonto" — und die Datenträger-Anforderung wird erfüllt.
- **Messbar:** Vault anlegen, auf zweitem Rechner öffnen, Aufgabe fortsetzen; Test prüft, dass ohne
  Schlüssel nichts lesbar ist (kein Klartext-Secret auf dem Stick).

---

## 2. Agent-Qualität (Kategorie B)

| # | Idee | Was/Warum | Aufwand | Messbar |
| --- | --- | --- | --- | --- |
| B1 | **Reviewer-Pass** | Zweiter Durchgang: der Agent kritisiert seinen eigenen Diff gegen Plan + Konventionen, belegt jede Beanstandung mit Zeile, korrigiert oder markiert | M | Anteil gefundener echter Fehler pro 10 Diffs; keine Beanstandung ohne Zeilenbeleg |
| B2 | **TDD-Modus** | Erst einen fehlschlagenden Test schreiben, dann implementieren, dann grün — der Test ist Teil der Evidenz | S | Anteil Aufgaben mit „rot → grün"-Nachweis |
| B3 | **Diff-First-UI** | Hunks einzeln annehmen/ablehnen, Inline-Kommentar „so nicht", „revise this hunk" → Agent plant nur diesen Hunk neu | L | Zeit bis akzeptierter Diff; Anteil Aufgaben ohne Nacharbeit |
| B4 | **Flaky- & Fehlerarchäologie** | Historie auswerten: welcher Test flakt wie oft, welche Signatur tritt wieder auf, welcher Fix half | M | Erkennungsrate auf injizierten Flakes; „known issues"-Liste mit Häufigkeit |
| B5 | **Repair-Bibliothek** | Gelernte Fix-Muster mit Erfolgsquote, aus der eigenen Historie, vor dem Modell abgefragt | M | Reparaturquote nach Signatur vor/nach Einführung |
| B6 | **Hybrid-Planer bewusst** | Deterministisch-first bleibt Standard; Modell nur für Sprachverständnis/Formulierung; Strategie dokumentieren und messen | S | Qualität ohne Modell ≥ X; Qualität mit Modell ≥ Y (beide veröffentlicht) |
| B7 | **Datei-Leases** | Mehrere Aufgaben parallel, aber exklusive Schreibrechte pro Datei/Bereich, Konflikte werden erkannt und gemeldet statt zerschrieben | M | 0 verlorene Änderungen bei 3 parallelen Aufgaben |
| B8 | **Undo/Redo pro Aufgabe** | Der FileJournal hat die Vorimages — daraus „letzten Schritt zurücknehmen", „ganze Aufgabe zurückrollen" | S | Round-Trip-Test: anwenden → zurückrollen → Diff leer |

---

## 3. Modelle & Training auf der RTX 4060 Ti (8 GB) (Kategorie C)

| # | Idee | Was/Warum | Aufwand | Messbar |
| --- | --- | --- | --- | --- |
| C1 | **Rollen-Router** | Kleine lokale Modelle je Rolle (Klassifizieren 1–2 B, Editieren 7 B 4-bit, Review 7 B, Testschreiben 3 B, Embeddings) mit VRAM-Budget, Laden/Entladen im Wechsel statt gleichzeitig | L | VRAM-Spitze < 8 GB; Qualität je Rolle einzeln gemessen |
| C2 | **Shadow-A/B im Alltag** | Kandidaten-Adapter bekommt X % echter Aufgaben im Schattenmodus; Erfolgsquote/Treue gegen aktiven Adapter, automatische Rücknahme bei Verschlechterung | M | Entscheidung nach n Aufgaben mit Konfidenz; kein Nutzer-Risiko |
| C3 | **Curriculum + Checkpoint-Auswahl** | Datensätze nach Schwierigkeit staffeln, Early Stopping, bester Checkpoint nach Benchmark statt letzter | M | Benchmark-Score je Trainingsminute steigt; Checkpoint-Wahl reproduzierbar |
| C4 | **Präferenzlernen aus Korrekturen** | Nutzer ändert Diff → Paar (schlecht, besser) speichern; zuerst als „Preference Memory" (billig, sofort wirksam), später DPO-lite auf 8 GB | L | Anteil akzeptierter Diffs steigt nach N Korrekturen |
| C5 | **Grammatik-erzwungene Tool-Calls** | Tool-Aufrufe nicht frei generieren, sondern per JSON-Schema-Grammatik (llama.cpp GBNF) → keine erfundenen Tools/Argumente | S | 0 Schemafehler bei 100 Modellaufrufen |
| C6 | **Lokale Embeddings** | Embedding-Modell (bge-small) für Suche/Retrieval, gemessen gegen FTS5: Recall@10, Latenz, RAM | M | Vergleichstabelle; Entscheidung nach Daten, nicht Meinung |
| C7 | **QLoRA-Beweis** | Ein echter 7B-4bit-Lauf auf der 4060 Ti inkl. Lernkurve im GUI — der einzige heute ungeprüfte Pfad | M | vollständiger Lauf mit Loss/VRAM/Zeit im Report |
| C8 | **Datenherkunft** | Jedes Trainingsbeispiel weiß, aus welcher Aufgabe/Commit/Verifikation es kam (Löschbarkeit, „Recht auf Vergessen") | S | Export mit Herkunft; Löschen einer Aufgabe entfernt ableitbare Beispiele |

---

## 4. Oberfläche & Premium-Gefühl (Kategorie D)

| # | Idee | Was/Warum | Aufwand |
| --- | --- | --- | --- |
| D1 | **Tray + Benachrichtigungen** | Icon in der Leiste, Meldung „Training fertig", „3 Vorschläge", „PR offen"; Doppelklick öffnet die Ansicht | S |
| D2 | **Command Palette** | Ctrl+K: Aufgaben starten, Ansichten wechseln, Läufe steuern, Einstellungen suchen | S |
| D3 | **„Warum?"-Panel** | Pro Entscheidung sichtbar: welche Memories, welche Knowledge-Treffer, welche Advisor-Konfidenz | M |
| D4 | **Live-Ansicht filtern** | Tool-Output nach Fehler/Warnung/Datei filtern, Suche, „nur Änderungen zeigen", Zoom | S |
| D5 | **Themes & Barrierefreiheit** | Hell/OLED-Schwarz, Akzentfarbe, Schriftgröße, reduzierte Animationen, Kontrastmodus | S |
| D6 | **Deutsch zuerst (i18n)** | Alle sichtbaren Texte über Katalog, DE als Default, EN als Zweitsprache; Datum/Zahlen lokal | M |
| D7 | **Onboarding** | Beim ersten Start: Hardware erkannt → Empfehlung (Modell, Methoden), 3 Klick bis zur ersten Aufgabe | S |
| D8 | **Fortschritt ehrlich** | Unbekannte Dauer zeigt „läuft seit X" statt Prozentbalken mit Fantasiewert (bereits teilweise umgesetzt) | S |

---

## 5. Sicherheit, Robustheit, Betrieb (Kategorie E)

| # | Idee | Was/Warum | Aufwand | Messbar |
| --- | --- | --- | --- | --- |
| E1 | **Sandbox-Härtung** | Landlock + seccomp + Netzwerk-Namespace pro Workspace; Profil „untrusted code" mit harten Garantien | L | Angriffs-Suite (35 Tests: Schreiben außerhalb, Netz, Fork-Bombe, ENV-Auslesen) bleibt grün |
| E2 | **Watchdog & Selbstheilung** | Aufsicht startet die App nach Absturz neu, setzt Aufgaben fort; DB-Integritätsprüfung + rotierende Backups + Restore-Test | M | Kill -9 mitten in einer Aufgabe → Fortsetzung ohne Datenverlust |
| E3 | **Secrets-Vault & Rotation** | Tokens nur verschlüsselt (age), nie im Klartext auf Platte, Rotationsablauf, Audit „welches Secret wurde wann benutzt" | M | Test: kein Klartext-Token in DB, Logs, Backups, Prozessliste |
| E4 | **Tool-SDK + Policy-Validierung** | Neue Tools deklarativ (Schema, Risikoklasse, Sandboxbedarf, eigene Tests) ablegen; `codemind tool new` legt Gerüst + Test an; Policy lehnt unklassifizierte Tools ab | M | Ein Fremd-Tool von Null bis grün in < 15 Minuten |
| E5 | **Packaging** | `.deb`/AppImage, systemd-User-Unit für headless Training, Autostart-Option | M | Frische Ubuntu-VM: installieren, `python3 main.py` läuft, Training als Dienst |
| E6 | **Selbstprofilierung** | Wo verliert der Agent Zeit? (Wartezeit auf Modell, Tests, Analyse) als eigene Ansicht | S | Top-3-Engpässe mit Zahlen, daraus abgeleitete Optimierungen |
| E7 | **Ressourcen-Vertrag** | Harte Obergrenzen pro Modus (z. B. „Agent darf nie mehr als 60 % CPU"), im Governor erzwungen und im GUI sichtbar | S | Lasttest: Grenze wird nie überschritten |

---

## 6. Reichweite (Kategorie F, freiwillig)

- **F1** Bench-Ergebnisse je Hardware-Tier veröffentlichen (A3) — **S**
- **F2** 3-Minuten-Demo (Bildschirmvideo) + GIFs im README, ohne Sprech-Ton — **S**
- **F3** „Rezepte"-Sammlung pro Projekttyp (Python-Lib, Web-API, Datenpipeline) als Vorlagen — **M**
- **F4** Beitragen leicht machen: Issue-Templates, CHANGELOG, „good first issue"-Liste, Übersetzungen — **S**

---

## 7. Was ich bewusst *nicht* machen würde (Anti-Scope)

- **Keine** Cloud-/API-Modelle, keine Telemetrie, kein Browser-Frontend — widerspricht der Identität.
- **Kein** Chat-zentriertes UI („frag den Bot") statt Aufgaben-orientiertem Ablauf.
- **Keine** großen Trainingsläufe ohne Datenqualität — wäre Rechenzeit-Theater.
- **Kein** zweites halbes Modell-Setup parallel zum ersten; erst C1/C7 sauber beweisen.
- **Keine** Feature-Zählung als Erfolgskriterium; Erfolg ist „beweisbar ehrlich" (A1/A3).

---

## 8. Reihenfolge (Fahrplan)

| Phase | Inhalt | Ergebnis nach der Phase |
| --- | --- | --- |
| **1 — Profil schärfen** (1–2 Abende) | A1 Change Passport, A3 Honesty-Rate + `--bench`, B2 TDD-Modus | CodeMind kann beweisen, was es getan hat — als Datei, nicht als Behauptung |
| **2 — Alltag** (1 Woche) | B3 Diff-First-UI, B8 Undo/Redo, D1 Tray, D2 Palette, B4 Flaky-Analyse | Tägliches Arbeiten fühlt sich wie ein Werkzeug an, nicht wie eine Demo |
| **3 — Tiefe** (1–2 Wochen) | A4 Projektgedächtnis + Verfall, D3 „Warum?", B1 Reviewer-Pass, A2 Replay | Der Agent wird auf *deinem* Repo messbar besser |
| **4 — Modell & Nacht** (1–2 Wochen) | C7 QLoRA-Beweis, C1 Rollen-Router, C2 Shadow-A/B, A5 persönlicher CI | Es läuft nachts etwas Nützliches und morgens liegt eine Liste vor |
| **5 — Härte & Reichweite** | E1 Sandbox-Härtung, E2 Watchdog, E3 Vault, E4 SDK, E5 Packaging, F1/F2 | Installierbar, absicherbar, zeigbar |

---

## 9. Offene Entscheidungen (brauchen dich, bevor Phase 2 startet)

1. **Nordstern:** A (beweisbar ehrlich), B (Projektgedächtnis) oder C (persönlicher CI) zuerst groß machen?
2. **Hauptnutzung:** eigene Projekte im Alltag (→ B3, B4, D1) oder öffentliche Wirkung/Demo (→ A1, A3, F1/F2)?
3. **Deutsch oder Englisch** als Standardsprache der Oberfläche? (D6 hängt davon ab)
4. **Verschlüsselter Vault** (A6/E3) gewünscht, oder bleibt der Stick ein reiner Datenordner?
5. **Modelle:** llama.cpp/GGUF oder Ollama als Hauptweg? (bestimmt C1/C5-Details)
