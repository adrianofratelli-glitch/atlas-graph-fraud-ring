# Modelagem de Dados — `schema/collections.md`

## Coleções

### `people`
```json
{
  "_id": "uuid-determinístico",
  "name": "string",
  "document_id": "cpf sintético, nunca real",
  "phones": ["string"],
  "addresses": [{ "street": "string", "city": "string", "zip": "string" }],
  "risk_flags": ["string"],
  "ring_id": "string | null",
  "created_at": "date"
}
```

### `accounts`
```json
{
  "_id": "uuid-determinístico",
  "person_id": "ref people._id",
  "account_type": "checking | credit_card | pix_key",
  "opened_at": "date",
  "status": "active | flagged | under_investigation",
  "ring_id": "string | null"
}
```

### `devices`
```json
{
  "_id": "device_id (fingerprint sintético)",
  "device_type": "mobile | web",
  "first_seen": "date"
}
```

### `transactions`
```json
{
  "_id": "uuid-determinístico",
  "from_account": "ref accounts._id",
  "to_account": "ref accounts._id",
  "device_id": "ref devices._id",
  "amount": "number",
  "reason_text": "string (campo livre — usado na demo de vector search)",
  "reason_embedding": "[float] (Voyage embedding)",
  "timestamp": "date"
}
```

### `connections` (padrão de edges explícitas)
```json
{
  "_id": "uuid",
  "from": "ref (people._id ou accounts._id)",
  "to": "ref (people._id ou accounts._id)",
  "type": "shares_device | shares_address | shares_pix_key | family_declared",
  "weight": "number (força da conexão, ex: nº de transações)",
  "created_at": "date"
}
```

## Dois padrões de modelagem de grafo — quando usar cada um

### Padrão A — Atributo compartilhado como aresta implícita

Não existe uma coleção `connections` para esse tipo de relação. A "aresta" é inferida em tempo de query: duas contas que compartilham o mesmo `device_id` estão implicitamente conectadas.

**Quando usar:** quando a relação já é um atributo natural do dado operacional (dispositivo, endereço, telefone) e não faz sentido de negócio mantê-la como uma entidade própria. Vantagem: zero duplicação, zero manutenção de sincronismo. Desvantagem: o `$graphLookup` precisa fazer lookup em `devices`/`addresses` a cada salto, o que é mais custoso do que seguir uma aresta explícita já materializada.

Exemplo de query (ver `queries/02_graphlookup_shared_attributes.js` para a versão completa):
```javascript
db.accounts.aggregate([
  { $match: { _id: "conta-suspeita-id" } },
  {
    $graphLookup: {
      from: "transactions",
      startWith: "$_id",
      connectFromField: "device_id",
      connectToField: "device_id",
      as: "network",
      maxDepth: 4,
      depthField: "hops"
    }
  }
])
```

### Padrão B — Edges explícitas em `connections`

A relação é materializada como um documento próprio, com peso e tipo. É o padrão mais próximo de um banco de grafo tradicional.

**Quando usar:** quando a relação em si carrega metadado relevante de negócio (peso, tipo, data de criação, quem declarou a relação — ex: "família declarada" no cadastro), ou quando o traversal via atributo implícito ficaria caro demais em fan-out alto.

```javascript
db.people.aggregate([
  { $match: { _id: "pessoa-suspeita-id" } },
  {
    $graphLookup: {
      from: "connections",
      startWith: "$_id",
      connectFromField: "from",
      connectToField: "to",
      as: "network",
      maxDepth: 4,
      depthField: "hops",
      restrictSearchWithMatch: { type: { $ne: "family_declared" } }
    }
  }
])
```

**Recomendação da POV:** usar o Padrão B (`connections`) para o núcleo da investigação (é mais barato e mais controlável via `restrictSearchWithMatch`), e o Padrão A apenas como fonte para *popular* `connections` (job que materializa atributos compartilhados como edges, com um limiar mínimo de força — ex: só materializa "shares_device" se o dispositivo foi usado por menos de 50 contas, para evitar hubs genéricos virando aresta de investigação).

## Índices obrigatórios (ver `schema/indexes.js` para o script completo)

| Coleção | Índice | Motivo |
|---|---|---|
| `connections` | `{ from: 1 }` | `connectFromField` do `$graphLookup` |
| `connections` | `{ to: 1 }` | `connectToField` do `$graphLookup` |
| `connections` | `{ type: 1, from: 1 }` | suporte a `restrictSearchWithMatch` |
| `transactions` | `{ device_id: 1 }` | traversal via Padrão A |
| `transactions` | `{ from_account: 1 }`, `{ to_account: 1 }` | queries operacionais + entrada do grafo |
| `accounts` | `{ ring_id: 1 }` | validação de ground truth na demo |
| `people` | Atlas Search index (`dynamic`, `autocomplete` em `name`) | entity resolution difusa |
| `transactions` | Vector Search index em `reason_embedding` | similaridade semântica |

## Resiliência da modelagem

- Todo `_id` é determinístico (hash de atributos-chave), não `ObjectId` aleatório — isso é o que permite o gerador de dados ser idempotente (rodar duas vezes não duplica).
- `ring_id` existe em `people`, `accounts` e é propagável a `connections` — serve como *ground truth* rastreável para validar que a demo sempre encontra a rede esperada, independente de aleatoriedade do gerador.
