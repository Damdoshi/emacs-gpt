# Local JSON-lines protocol, version 1

Run `chatgpt-web bridge`. Each UTF-8 request is one JSON object followed by LF:

```json
{"id":1,"method":"open","params":{"url":"https://chatgpt.com/c/abcdefgh"}}
```

The response has the same ID and either `result` or `error`:

```json
{"id":1,"result":{"url":"https://chatgpt.com/c/abcdefgh","title":"Titre","messages":[],"files":[]}}
{"id":1,"error":{"code":"login_required","message":"Connexion requise."}}
```

An initial `{"event":"ready","protocol":1,"version":"0.1.0"}` announces the
protocol process, not a connected browser session. Browser startup is lazy.
While a send is in progress, `event:"snapshot"`, `id` and `data` carry updated
conversation snapshots. Events never trigger a local script or patch action.

| Method | Parameters |
| --- | --- |
| `ping` | None |
| `open` | Optional conversation `url`; absent means new conversation |
| `snapshot` | Optional conversation `url`; absent means the new-conversation page |
| `conversations` | None; returns currently loaded sidebar links, marked partial |
| `send` | `text`, optional conversation `url` |
| `stop` | None; can interrupt an active send |
| `upload` | `paths` array of local absolute files, optional `url` |
| `download` | File `ref`, exact `href`, absolute local `destination`, optional `url` |
| `auth_begin` | None |
| `auth_state` | None |
| `auth_action` | Current control `ref`, optional `value` |

Snapshots contain messages with `role`, `id` and ordered `blocks`. A block has
`type:"text"` or `type:"code"`; code additionally has `language` and its exact
`text`. Files and authentication controls have transient references. The adapter
checks that a control or file still matches before acting on it.

Requests other than `ping` and `stop` are serialized. A client keeps stdin open
while waiting. EOF cancels pending operations and closes the browser gracefully.
Requests above 16 MiB are refused. There is no network listener. Do not log the
protocol: auth input, messages and signed file links can be private.
