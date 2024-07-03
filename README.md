# 4d-plugin-get-text-input
Wait for stop character and return buffer; for USB HID devices (e.g. barcode scanner)

![version](https://img.shields.io/badge/version-18%2B-EB8E5F)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/get-text-input)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/get-text-input/total)

## Syntax

```4d
GET TEXT INPUT(stopcode;method;textArg;intArg)
```

Parameter|Type|Description
------------|------------|----
stopcode|TEXT|character to wait for, or ``""`` to stop waiting
method|TEXT|method to invoke (must be in the current structure, not a component
textArg|TEXT|argument to pass to the method (``$2``); typically, a worker/process name
intArg|LONGINT|argument to pass to the method(``$3``); typically, a window reference

## Examples

```4d
GET TEXT INPUT ("\r";"get_text_and_call_form";Current process name;Current form window)
```

### Example callback method

```4d
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
