# lerim project

Register or remove repositories.

## Summary

Projects are path registrations.
They are not storage roots.

## Examples

```bash
lerim project add .
lerim project add ~/codes/my-app
lerim project list
lerim project remove my-app
```

## How it works

Lerim stores the project path in user config.
Durable context still lives in the global database.
