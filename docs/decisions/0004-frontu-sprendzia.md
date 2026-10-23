# ADR 0004: Frontu sprendžia, ką priima

- Data: 2026-10-23
- Būsena: Priimtas (savininkas)
- Susiję: `contracts/frontu/`, `src/services/task_refs.py`

## Sprendimas

The Center does not pre-validate what Frontu validates; it sends and shows Frontu's answer (BC allows Deal Type no_vin).

Centras iš anksto netikrina to, ką tikrina Frontu; jis siunčia ir rodo Frontu atsakymą (BC leidžia Deal Type `no_vin`).

## Pasekmės

- Jei Frontu nerandama transporto priemonė, užduotis siunčiama be `workspace`, kaip anksčiau. Pati paieška (`/workspaces?systemId=`) lieka pagal kontraktą.
- Kodas `task.workspace_not_found` pašalintas.
- Frontu atsakymas į rašymą (užduotis, medžiaga, darbas):
  - HTTP 4xx (išskyrus 401/403/429) – Frontu atmetė įrašą: rezultatas `error`, kodas `task.frontu_rejected`, tipas `data`; priežastis – paties Frontu klaidos tekstas (`{"error": ...}`, kitaip atsakymo kūnas), išvalytas, ne ilgesnis nei 500 simbolių.
  - HTTP 5xx, 401/403/429 – sistemos klaida `system.frontu_error` (kaip anksčiau).
- Susiję: `src/clients/frontu_task_payloads.py` (`FrontuRejectedError`, `is_rejection`).
