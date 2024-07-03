//%attributes = {"invisible":true}
C_TEXT:C284($1; $text)
C_TEXT:C284($2; $worker)
C_LONGINT:C283($3; $window)

$text:=$1
$worker:=$2
$window:=$3

SET PROCESS VARIABLE:C370(Process number:C372($worker); Variable; $text)
POST OUTSIDE CALL:C329(Process number:C372($worker))

//CALL PROCESS(-1) is also used On Outside Call to update the display