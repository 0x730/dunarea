# Danube — release receipt, 10 septembrie 2026

Mecanismul este implementat și verificat local și live. Prima probă de pe
host este deployment-ul Forge `77396437`, revizia
`d155091ba056183b4d4370a8a639b6a7f3aca817`; detaliile sunt în ultima secțiune.

## Scope și punct de pornire

Cererea execută [promptul Danube](../../ops/0x730/spec/prompts/release-receipt/danube.md)
și [contractul comun](../../ops/0x730/spec/prompts/release-receipt/README.md),
versiunea 1.0 din 10 septembrie. Autoritatea inițială includea schimbarea
locală, testarea și commit-ul. După commit, operatorul a autorizat explicit
push și deploy. Nu există `CLAUDE.md`, `CURRENT`
sau cadru PM local; sunt păstrate [AGENTS.md](../AGENTS.md),
[README.md](../README.md), [DEPLOY.md](../DEPLOY.md) și [indexul ops](README.md).

- Repository: `/home/dacrise/0x730/danube`, `0x730/dunarea`, branch `main`.
- HEAD inițial: `91a95d7ca6a8cc2e0fb59be4f3fbbe106556b808`.
- Worktree inițial: curat, fără schimbări tracked sau untracked de păstrat.
- Sursa Ops: HEAD `f604e0dfe9018680ff479f77e5322f240bc5fe4b`; writer-ul nu
  avea schimbări locale la copiere.

## Implementarea refolosită

`ops/write_build_revision.py::write_revision()` scrie deja atomic SHA-ul Git
în `--output`. Forge îi capturează stdout în `RELEASE_SHA`, apoi rulează
testele înainte de activare. Capabilitatea lipsă era consemnarea receipt-ului
comun în același director de release.

Am copiat **verbatim**, fără port Python,
[`packages/fleet/kit/write-release-receipt.mjs`](../../ops/packages/fleet/kit/write-release-receipt.mjs)
în [ops/write-release-receipt.mjs](write-release-receipt.mjs). SHA-256 identic
pentru sursă și copie:

```text
62b385d083478c568e3988f8f56da3546f1436c8e0680e33c8349d7e3b84c48d
```

Hook exact: `main()` din [write_build_revision.py](write_build_revision.py)
apelează noul `write_release_receipt(repository, revision)` după scrierea
atomică existentă și înainte să imprime SHA-ul. Acesta rulează `node` cu
calea copiei canonice, `cwd=repository`, fără argumente de artefact și cu
`FORGE_DEPLOY_SHA` fixat la SHA-ul checkout-ului deja verificat. Astfel un SHA
vechi moștenit din mediu nu produce identități diferite în cele două fișiere.
Stdout-ul Node este redirecționat către stderr-ul entrypoint-ului, păstrând
linia de receipt în log și stdout-ul Python exclusiv pentru `RELEASE_SHA`.
Lipsa Node, un exit non-zero sau timeout-ul de 30 s opresc pregătirea.

Contractul canonic rămâne intact: rădăcină Git, îmbinare în aceeași revizie,
receipt nou la alt SHA, artefact lipsă consemnat explicit și refuz fără
revizie. `.release-receipt.json` este gitignored, per release, fără shared
path. Receipt-ul este scris înainte de teste și activare; existența lui
identifică pregătirea, fără a certifica succesul deployment-ului.

## Mapping pentru manifestul Ops

| Site / grup | Artefacte declarate | Motiv |
| --- | --- | --- |
| `3331936` / `dunarea.info` / `web-api` | `{}` | Nu există compilare, bundler sau `package.json`; Python și `static/` sunt livrate direct. `.build-revision` este un stamp variabil al SHA-ului, iar SQLite/cache-urile sunt date runtime, nu output determinist de build. |

## Readback operațional, numai citire

După citirea entrypoint-ului DevOps, hărții și manifestului Danube din Ops:

- GET al resursei dedicate Forge
  `/orgs/daniel-vladescu-nud/servers/949568/sites/3331936/deployments/script`:
  `auto_source=false`, comanda existentă este exact cea de mai jos și este
  urmată de `python3 -m unittest discover -s tests`.
- SHA-256 al conținutului scriptului Forge citit:
  `404d04151b4cc805c89ace0b1c62fd4f4173867b9692dfad3bf1eeadb03f37c4`.
- SSH neinteractiv ca `dunarea`: `/usr/bin/node`, `v22.23.2`;
  checkout-ul activ era la `91a95d7ca6a8cc2e0fb59be4f3fbbe106556b808` și
  `.release-receipt.json` era absent din `current`.

```bash
RELEASE_SHA="$(python3 ops/write_build_revision.py --repository "$FORGE_RELEASE_DIRECTORY" --output "$FORGE_RELEASE_DIRECTORY/.build-revision")"
```

Au fost citite numai câmpurile necesare; nu s-au păstrat configurații private
sau răspunsuri provider brute. GET-ul Forge a necesitat reîncercare în afara
sandbox-ului după eroarea DNS. Scriptul Forge nu a fost editat, iar pe host
nu s-a instalat sau rulat cod nou. Node existent este folosit numai la
pregătirea release-ului și în teste; runtime-ul Python nu depinde de el.

## Verificări locale

Danube nu are build de producție. A fost executată comanda reală de pregătire
de mai sus, cu `FORGE_RELEASE_DIRECTORY` fixat la checkout-ul local, păstrând
capturarea shell folosită de Forge. Nu a fost apelat manual numai writer-ul
Node pentru a pretinde că hook-ul funcționează.

Logul pregătirii locale:

```text
release receipt 91a95d7ca6a8 0 artifact(s) -> .release-receipt.json
```

Receipt-ul local a avut `schemaVersion=1`,
`revision=91a95d7ca6a8cc2e0fb59be4f3fbbe106556b808`, `artifacts={}`,
`builtAt=2026-09-10T09:52:34.666Z`, `node=v24.13.0`. SHA-ul capturat a
coincis cu Git HEAD și `.build-revision`, iar `git check-ignore` a confirmat
regula receipt-ului. Acesta este HEAD-ul de dinaintea commit-ului acestei
schimbări, cu implementarea nouă în worktree; nu este o probă de cod livrat.

- `python3 -m unittest discover -s tests -v`: **152 teste, OK**, inclusiv
  șapte teste noi cu fixture-uri Git temporare. Verifică hook-ul real,
  stdout/stderr, identitatea Git în prezența unui SHA moștenit diferit,
  oprirea pregătirii la eșecul Node, rădăcina dintr-un subdirector,
  hash/mărime, artefacte lipsă/directoare, îmbinare, reset la altă revizie,
  lipsa repo-ului/commit-ului și receipt corupt.
- Prima rulare restricționată a eșuat: `spawnSync git EPERM` în lanțul
  Python → Node → Git era prins de writer drept lipsă repo. Aceeași suită și
  aceeași pregătire au trecut în afara sandbox-ului, fără modificarea codului.
- Copia canonică este identică byte cu byte; `git diff --check`: PASS.
- 45 de linkuri/ancore locale și 16 exemple Bash: PASS. Cele două macro-uri
  Forge `$CREATE_RELEASE()` / `$ACTIVATE_RELEASE()` au fost neutralizate
  numai pentru `bash -n`, fiind expandate de provider înainte de Bash în
  producție; exemplele din documentație nu au fost modificate sau executate.

## Prima probă live — push și deploy autorizate ulterior

Commit-ul implementării `d155091ba056183b4d4370a8a639b6a7f3aca817` a fost
împins pe `main`. HEAD, `origin/main` și `git ls-remote origin refs/heads/main`
au coincis înainte de deploy. Readback-ul Forge a confirmat repo/branch/user,
`quick_deploy=false`, scriptul dedicat neschimbat și procesul `1006295`.
Colecția completă de deployment-uri nu avea execuții în curs.

POST-ul explicit a întors HTTP `202`, ID **`77396437`**, revizia cerută și
starea `queued`; același ID a fost urmărit până la **`finished`**.
`created_at=2026-09-10T10:00:18.000000Z`,
`started_at=2026-09-10T10:00:33.000000Z`; câmpul `finished_at` nu era furnizat.
Resursa separată de log a confirmat:

```text
release receipt d155091ba056 0 artifact(s) -> .release-receipt.json
Ran 152 tests in 4.697s
OK
daemon-1006295:daemon-1006295_00: stopped
daemon-1006295:daemon-1006295_00: started
```

Citirea ca `dunarea` a confirmat fișierul gitignored, fără symlink/shared path,
în `/home/dunarea/dunarea.info/releases/77396437/.release-receipt.json`:

```json
{
  "schemaVersion": 1,
  "revision": "d155091ba056183b4d4370a8a639b6a7f3aca817",
  "artifacts": {},
  "builtAt": "2026-09-10T10:00:43.879Z",
  "node": "v22.23.2"
}
```

Writer-ul de pe host are același SHA-256 canonic documentat mai sus.
La `10:03:13 UTC`, Git HEAD din release, receipt-ul, `.build-revision`,
health pe loopback și health public au același SHA `d155091ba056…`.
Public: HTTP 200, `status=ok`, `version=1.1.0`, `warmup_done=true`.

`ops/verify_deploy.sh` a rulat ca `dunarea` din release-ul activ, cu
argumentele complete din [DEPLOY.md](../DEPLOY.md#8-acceptanță-post-deploy):
exit **0**, toate verificările esențiale trec. Backupul local din 10 septembrie
avea 6 h, integritate validă și 0600; obiectul off-box era proaspăt și citit
autentificat. Au trecut identitatea runtime, permisiunile, binding-ul loopback,
restricția originii, anteturile Cloudflare și contractul `security.txt`.

Patru avertismente rămân separate de exit code: cele trei despre crontab sunt
reconciliate cu joburile Forge `2117004`, `2117005`, `2120262`, `2120431`,
toate `installed`, user `dunarea`, programări neschimbate. UFW nu era lizibil
utilizatorului izolat; starea firewall-ului nu este recertificată prin acest
release. Nu s-a creat o rețetă root sau un job nou pentru această observație.

Funcția existentă `source_freshness.collect_evidence()` a citit loopback fără
scriere de status sau alerte: la `10:03:40 UTC`, **15 endpointuri**, stare
`fresh`, `staleSources=[]`, `failures=[]`, raportul de anomalii vechi de
21 557 s (sub pragul de 12 h).

Spot-check-ul timestamp-urilor a găsit aceleași observații PegelOnline
`2026-09-10T11:45:00+02:00` în sumar și endpointul de stații; AFDJ avea
`2026-09-10T03:00:00+03:00`, GloFAS data `2026-09-10`, iar INHGA păstra
buletinul `2026-09-09`. Ultima dată nu a fost mutată artificial pe ziua
curentă; monitorul nu semnala fallback sau eroare de fetch și
`overview.errors` era gol. Starea `fresh` este evaluarea transportului și a
cache-ului, nu afirmația că fiecare observație are data zilei curente.
Evidența existentă de pe host confirma monitorul backup `fresh` la
`08:17:01 UTC` și jobul source freshness `fresh` la `09:25:05 UTC`.
După primul deploy existau exact două directoare Danube: `77396437` activ și
`77322798` rollback.

Aceasta este proba primei activări a mecanismului, nu o afirmație permanentă
despre branch tip. Commit-ul ulterior care consemnează aceste rezultate
trebuie verificat separat dacă este livrat. Nu s-au schimbat scriptul Forge,
edge-ul, configurația hostului sau joburile recurente; deploy-ul a reutilizat
restartul și prunerul Danube existente. Nu s-a trimis alertă și nu s-a rulat
restore-drill; primirea mesajelor și restaurarea rămân probe separate.
