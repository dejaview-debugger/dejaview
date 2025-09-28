# Usage

## Command line

To run the test:
```
python3 -m dejaview tests/test.py
```

To kill leftover processes:
```
ps aux | grep 'python3 -m dejaview' | awk '{print $2}' | xargs kill
```

## In VS Code
1. Open djv-test folder in VS Code
2. Press `F5` to open a new window with your extension loaded.
3. In the new VS Code window from the previous step, open `tests` folder.
4. Run the script you want to debug from the by going to the "Run and Debug" tab (`Ctrl+Shift+D` or `Cmd+Shift+D` on Mac) and click the green arrow.
