# 4d-plugin-get-text-input
Wait for stop character and return buffer; for USB HID devices (e.g. barcode scanner)

### Platform

| carbon | cocoa | win32 | win64 |
|:------:|:-----:|:---------:|:---------:|
|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|

### Version

<img src="https://cloud.githubusercontent.com/assets/1725068/18940649/21945000-8645-11e6-86ed-4a0f800e5a73.png" width="32" height="32" /> <img src="https://cloud.githubusercontent.com/assets/1725068/18940648/2192ddba-8645-11e6-864d-6d5692d55717.png" width="32" height="32" />

## Syntax

```
GET TEXT INPUT(stopcode;method;textArg;intArg)
```

Parameter|Type|Description
------------|------------|----
stopcode|TEXT|character to wait for, or ``""`` to stop waiting
method|TEXT|method to invoke (must be in the current structure, not a component
textArg|TEXT|argument to pass to the method (``$2``); typically, a worker/process name
intArg|LONGINT|argument to pass to the method(``$3``); typically, a window reference

## Examples

```
GET TEXT INPUT ("\r";"get_text_and_call_form";Current process name;Current form window)
```

### Example callback method

```
C_TEXT($1;$text)
C_TEXT($2;$worker)
C_LONGINT($3;$window)

Case of 
	: (Count parameters=3)
		
		$text:=$1
		$worker:=$2
		$window:=$3
		
		CALL FORM($window;Current method name;$text)
		
	: (Count parameters=1)
		
		$Variable:=OBJECT Get pointer(Object named;"Variable")
		$Variable->:=$1
		
End case 
```
