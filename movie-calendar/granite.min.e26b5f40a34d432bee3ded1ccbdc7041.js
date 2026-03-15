var _____WB$wombat$assign$function_____ = function(name) {return (self._wb_wombat && self._wb_wombat.local_init && self._wb_wombat.local_init(name)) || self[name]; };
if (!self.__WB_pmw) { self.__WB_pmw = function(obj) { this.__WB_source = obj; return this; } }
{
  let window = _____WB$wombat$assign$function_____("window");
  let self = _____WB$wombat$assign$function_____("self");
  let document = _____WB$wombat$assign$function_____("document");
  let location = _____WB$wombat$assign$function_____("location");
  //let top = _____WB$wombat$assign$function_____("top");
  let parent = _____WB$wombat$assign$function_____("parent");
  let frames = _____WB$wombat$assign$function_____("frames");
  let opener = _____WB$wombat$assign$function_____("opener");

(function(c,b,d){var a;
b.Granite=b.Granite||{};
b.Granite.$=b.Granite.$||c;
b._g=b._g||{};
b._g.$=b._g.$||c;
a=Granite.HTTP;
c.ajaxSetup({externalize:true,encodePath:true,hook:true,beforeSend:function(f,e){if(typeof G_IS_HOOKED=="undefined"||!G_IS_HOOKED(e.url)){if(e.externalize){e.url=a.externalize(e.url)
}if(e.encodePath){e.url=a.encodePathOfURI(e.url)
}}if(e.hook){var g=a.getXhrHook(e.url,e.type,e.data);
if(g){e.url=g.url;
if(g.params){if(e.type.toUpperCase()=="GET"){e.url+="?"+c.param(g.params)
}else{e.data=c.param(g.params)
}}}}},statusCode:{403:function(e){if(e.getResponseHeader("X-Reason")==="Authentication Failed"){a.handleLoginRedirect()
}}}});
c.ajaxSettings.traditional=true
}(jQuery,this));

}
/*
     FILE ARCHIVED ON 20:31:01 Feb 14, 2018 AND RETRIEVED FROM THE
     INTERNET ARCHIVE ON 13:23:07 Oct 28, 2024.
     JAVASCRIPT APPENDED BY WAYBACK MACHINE, COPYRIGHT INTERNET ARCHIVE.

     ALL OTHER CONTENT MAY ALSO BE PROTECTED BY COPYRIGHT (17 U.S.C.
     SECTION 108(a)(3)).
*/
/*
playback timings (ms):
  captures_list: 0.552
  exclusion.robots: 0.026
  exclusion.robots.policy: 0.017
  esindex: 0.01
  cdx.remote: 8.279
  LoadShardBlock: 461.099 (3)
  PetaboxLoader3.resolve: 595.949 (4)
  PetaboxLoader3.datanode: 199.402 (5)
  load_resource: 352.023 (2)
*/