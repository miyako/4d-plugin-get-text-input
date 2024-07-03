Case of 
	: (Form event code:C388=On Load:K2:1)
		
		GET TEXT INPUT("\r"; "get_text_and_call_process"; Current process name:C1392; Current form window:C827)
		
	: (Form event code:C388=On Unload:K2:2)
		
		GET TEXT INPUT
		
	: (Form event code:C388=On Outside Call:K2:11)
		
		POST OUTSIDE CALL:C329(-1)
		
End case 