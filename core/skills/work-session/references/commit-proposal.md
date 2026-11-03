# Commit proposal format

Read this only when delivery is not authorized and commits must be proposed instead of created. Messages follow the `git-workflow` rule; check them with `verify-changes`.

Per commit:

````
### Commit N — <summary>
Files: <list; indicate partial hunks when a file is split>
Covered by: <session checks>

```
<emoji> <type>[(scope)]: <description>

<optional body>
```
````

If the user is absent, record the proposal in `<id>-result.md` and leave the changes uncommitted.
