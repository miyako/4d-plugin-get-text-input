//%attributes = {"invisible":true}
C_TEXT:C284($1; $text)
C_TEXT:C284($2; $worker)
C_LONGINT:C283($3; $window)

Case of 
	: (Count parameters:C259=3)
		
		$text:=$1
		$worker:=$2
		$window:=$3
		
		CALL FORM:C1391($window; Current method name:C684; $text)
		
	: (Count parameters:C259=1)
		
		$Variable:=OBJECT Get pointer:C1124(Object named:K67:5; "Variable")
		$Variable->:=$1
		
End case 