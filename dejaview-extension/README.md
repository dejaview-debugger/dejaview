# DejaView VS Code Extension README

This is the README for the DejaView VS Code extension.


## Running the extension for development

* Press `F5` to open a new window with your extension loaded.
* Run your command from the command palette by pressing (`Ctrl+Shift+P` or `Cmd+Shift+P` on Mac) and typing `Hello World`.
* Set breakpoints in your code inside `src/extension.ts` to debug your extension.
* Find output from your extension in the debug console.

---

## Running Extension Tests

To run the automated tests for the DejaView VS Code extension:

1. Open a terminal and navigate to the `dejaview-extension` directory:
	```
	cd /path/to/se390/dejaview-extension
	```
2. Install dependencies:
	```
	npm install
	```
3. Run the test suite:
	```
	npm test
	```

This will compile the extension and run all tests in `src/test/extension.test.ts`.

---

## Following extension guidelines

Ensure that you've read through the extensions guidelines and follow the best practices for creating your extension.

* [Extension Guidelines](https://code.visualstudio.com/api/references/extension-guidelines)
