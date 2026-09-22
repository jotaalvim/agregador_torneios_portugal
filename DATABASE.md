# Base de dados: como aceder, editar, exportar e não perder dados

Este projeto usa **SQLite** (não Postgres — ver nota no fim). Um único
ficheiro:

- Local (fora do Docker): `data/chess.db`
- Com Docker: dentro do volume `chess_data`, montado em `/app/data` no
  container (ver `docker-compose.yml`). Não existe como ficheiro na tua
  máquina — só dentro do volume.

O modo `WAL` está ligado (`app/database.py`), por isso vais ver também
`chess.db-wal` e `chess.db-shm` ao lado do `.db`. Isto é normal: enquanto a
app está a correr, parte dos dados mais recentes pode estar só no `-wal`,
não no `.db` principal. Interessa para os avisos de backup mais abaixo.

## 1. Como abrir a base de dados

### Localmente

```bash
sqlite3 data/chess.db
```

Comandos úteis dentro da consola do `sqlite3`:

```sql
.tables                          -- lista as tabelas
.schema tournaments               -- mostra a estrutura da tabela
.mode column
.headers on
SELECT id, title, date, status FROM tournaments ORDER BY date;
.quit
```

### Com Docker

O ficheiro só existe dentro do volume, e o container `web` **não** tem o
`sqlite3` instalado (só a app é que usa SQLite, via biblioteca Python, não
o CLI) — por isso `docker compose exec web sqlite3 ...` falha com
"command not found". Acede sempre através de um container temporário que
monta o mesmo volume, quer o `web` esteja a correr ou não:

```bash
docker run --rm -it -v chess_data:/data alpine sh -c \
  "apk add --no-cache sqlite && sqlite3 /data/chess.db"
```

### Com uma app gráfica (mais fácil para edição visual)

Qualquer uma destas abre ficheiros `.db` diretamente:

- **DB Browser for SQLite** (grátis, https://sqlitebrowser.org) — a mais
  simples para ver/editar linhas numa tabela tipo Excel.
- **TablePlus** — mais polido, também suporta Postgres/MySQL se um dia
  migrares.
- Extensão **SQLite Viewer** ou **SQLite** no VS Code — dá para abrir o
  `data/chess.db` sem sair do editor.

Se estiveres a usar Docker, copia o ficheiro para fora primeiro (secção 4)
antes de abrir numa app gráfica — a maioria não sabe ler de dentro de um
volume Docker diretamente.

## 2. Editar dados

Para o dia a dia (aprovar, rejeitar, editar, apagar torneios) usa sempre o
painel de admin em `/admin` — é validado e não corre o risco de deixar a
base de dados num estado inconsistente.

Editar diretamente por SQL só para correções pontuais que o painel não
cobre. Exemplos:

```sql
-- corrigir um campo
UPDATE tournaments SET location = 'Pavilhão Municipal' WHERE id = 3;

-- ver o que está pendente
SELECT id, title, submitted_by_name, submitted_by_email
FROM tournaments WHERE status = 'pending';

-- apagar um torneio (cuidado, isto não tem "undo")
DELETE FROM tournaments WHERE id = 7;
```

Notas importantes:
- `status` só aceita os valores `pending`, `approved` ou `rejected` (texto,
  minúsculas) — outro valor qualquer parte a app ao carregar essa linha.
- `date` tem de estar em formato `YYYY-MM-DD`.
- Se editares com a app a correr ao mesmo tempo, faz as alterações e sai da
  consola rapidamente — em WAL, escritas longas/abertas podem bloquear a
  app por alguns segundos (`busy_timeout` está a 5s, ver `database.py`).

## 3. Debugging

Perguntas típicas e a query que responde:

```sql
-- quantos torneios há por estado
SELECT status, COUNT(*) FROM tournaments GROUP BY status;

-- torneios de um distrito, mais recentes primeiro
SELECT id, title, date FROM tournaments
WHERE district = 'Braga' ORDER BY date DESC;

-- quando é que algo foi submetido
SELECT id, title, created_at FROM tournaments ORDER BY created_at DESC LIMIT 10;
```

Se algo parecer "não guardado" depois de mexeres via `sqlite3` com a app a
correr, força um checkpoint do WAL para garantir que o `.db` principal está
atualizado:

```sql
PRAGMA wal_checkpoint(FULL);
```

## 4. Exportar dados

### Para um ficheiro `.sql` (backup completo, reimportável)

```bash
sqlite3 data/chess.db .dump > backup.sql
```

Para restaurar a partir desse ficheiro noutro sítio:

```bash
sqlite3 novo.db < backup.sql
```

### Para CSV (para abrir em Excel/Sheets)

```bash
sqlite3 -header -csv data/chess.db "SELECT * FROM tournaments;" > tournaments.csv
```

### Com Docker

O mesmo aviso da secção 1 aplica-se aqui: usa o container temporário, não
`docker compose exec web` (o `web` não tem `sqlite3`).

```bash
docker run --rm -v chess_data:/data -v "$(pwd)":/backup alpine sh -c \
  "apk add --no-cache sqlite && sqlite3 /data/chess.db .dump > /backup/backup.sql"
```

## 5. Garantir que não perdes dados (backups)

**Nunca copies `chess.db` "à mão" (cp/scp) com a app a correr** — por causa
do WAL, o `.db` sozinho pode estar incompleto sem os ficheiros `-wal`/`-shm`
que o acompanham nesse momento. Usa sempre um dos métodos seguros abaixo.

### Opção segura 1 — comando `.backup` do próprio SQLite (recomendado)

Faz uma cópia consistente mesmo com a app a correr:

```bash
sqlite3 data/chess.db ".backup 'backup-$(date +%Y%m%d).db'"
```

Com Docker:

```bash
BACKUP_NAME="backup-$(date +%Y%m%d).db"
docker run --rm -v chess_data:/data -v "$(pwd)":/backup alpine sh -c \
  "apk add --no-cache sqlite && sqlite3 /data/chess.db '.backup /backup/$BACKUP_NAME'"
```

### Opção segura 2 — dump em SQL (secção 4) e guardar esse ficheiro

Um `backup.sql` gerado com `.dump` é sempre consistente e é texto simples,
por isso também é fácil de guardar num repositório privado ou enviar por
email para ti próprio.

### Rotina sugerida

- Antes de qualquer edição manual por SQL (secção 2), corre primeiro um
  `.backup`.
- De vez em quando (ex: uma vez por mês, ou antes de atualizares a app),
  exporta um `backup.sql` e guarda-o fora do servidor (o teu computador,
  uma pasta na cloud, etc.) — o volume `chess_data` sobrevive a
  `docker compose down`/rebuilds, mas não sobrevive a apagares o volume de
  propósito (`docker compose down -v`) ou a perderes a máquina/disco.

## Nota sobre Postgres

Hoje o projeto **não usa Postgres** — é só SQLite (ver `README.md`). O
`DATABASE_URL` em `app/config.py` já vem de uma variável de ambiente, por
isso mudar de motor de base de dados no futuro passaria por:

1. Ter um servidor Postgres e apontar `DATABASE_URL` para lá (ex.:
   `postgresql://user:pass@host/db`).
2. Instalar o driver (`psycopg2-binary` no `requirements.txt`).
3. Remover/ajustar o listener de `PRAGMA` em `app/database.py`, que é
   específico do SQLite.

Se um dia isso acontecer, todos os comandos `sqlite3` acima passam a ser
`psql` (`psql $DATABASE_URL`), e o `.dump`/`.backup` passam a ser
`pg_dump`/`pg_restore` — a lógica de "faz sempre um backup antes de editar
à mão" mantém-se igual.
