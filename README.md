To run the test:

```
python3 -m dejaview tests/test.py
```

To kill leftover processes:
```
ps aux | grep 'python3 -m dejaview' | awk '{print $2}' | xargs kill
```
