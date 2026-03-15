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

var dtCurrent = new Date();
var smallmonths = ["jan", "feb", "mar", "apr", "may", "june", "jul", "aug", "sep", "oct", "nov", "dec"];
var _daysName = ["&lt;&lt; Yesterday", "Tomorrow &gt;&gt", "&lt;&lt; Previous", "Next &gt;&gt"];
var _clickIndex = 0;
var _today = dtCurrent.getDate();

function formatSmallMonthDay() {
    return smallmonths[dtCurrent.getMonth()] + "-" + ('0' + dtCurrent.getDate()).slice(-2);
}
var currentMonthDay = formatSmallMonthDay();
function bindTomorrowAndYesterday() {
    $(".yesterday button").click(function() {
        _clickIndex++;

        dtCurrent.setDate(dtCurrent.getDate() - 1);
        var newSmallMonthDay = formatSmallMonthDay();
        var _dayShown = parseInt(newSmallMonthDay.split("-")[1]);
        var $movieContent = $("#" + newSmallMonthDay + " .expanded-movie").clone().append('<div class="yesterday"><button type="button">' +(_clickIndex < 3  || _dayShown == _today ?_daysName[0] : _daysName[2] ) + '</button></div><div class="tomorrow"><button type="button">' + (_clickIndex < 3 || _dayShown == _today ?_daysName[1]:_daysName[3] ) + '</button></div><div class="clearfix"></div>');
        $movieContent.find("#close-icon").remove();
        $("div.watch-on-this-day .expanded-movie").replaceWith($movieContent);
        bindTomorrowAndYesterday();
        if($(window).width() < 768 && _clickIndex > 2){
            var _topSection = $('div[data-anchor="top-section"]').offset().top - 10;
            $('html, body').animate({ scrollTop: _topSection }, 'slow');
            return false;
        }
    });
    $(".tomorrow button").click(function() {
        _clickIndex++;

        dtCurrent.setDate(dtCurrent.getDate() + 1);
        var newSmallMonthDay = formatSmallMonthDay();
        var _dayShown = parseInt(newSmallMonthDay.split("-")[1]);
        var $movieContent = $("#" + newSmallMonthDay + " .expanded-movie").clone().append('<div class="yesterday"><button type="button">' + (_clickIndex < 3 || _dayShown == _today ?_daysName[0]:_daysName[2] ) + '</button></div><div class="tomorrow"><button type="button">' + (_clickIndex < 3 || _dayShown == _today ?_daysName[1]:_daysName[3] ) + '</button></div><div class="clearfix"></div>');
        $movieContent.find("#close-icon").remove();
        $("div.watch-on-this-day .expanded-movie").replaceWith($movieContent);
        bindTomorrowAndYesterday();
        if($(window).width() < 768 && _clickIndex > 2){
            var _topSection = $('div[data-anchor="top-section"]').offset().top - 10;
            $('html, body').animate({ scrollTop: _topSection }, 'slow');
            return false;
        }
    });
}

$(function() {

    // Calendar
    function c() {
        p();
        var e = h();
        var r = 0;
        var u = false;
        l.empty();
        while (!u) {
            if (s[r] == e[0].weekday) {
                u = true
            } else {
                l.append('<div class="blank"></div>');
                r++
            }
        }
        for (var c = 0; c < 42 - r; c++) {
            if (c >= e.length) {
                l.append('<div class="blank"></div>')
            } else {
                var v = e[c].day;
                var m = g(new Date(t, n - 1, v)) ? '<div id="today" class="today calendar-day" data-scroll="'+  i[n - 1].toLowerCase() +'-' + v + '" >' : "<a href='#' class='calendar-day' data-scroll='" + i[n - 1].toLowerCase() + "-"+ v +"'><div '>";
                l.append(m + "" + v + "</div>")
            }
        }
        var y = o[n - 1];
        a.css("background-color", "#ffffff").find("h6").text(i[n - 1] + " " + t);
        f.find("div").css("color", "#222222");
        l.find(".today").css("background-color", "#222222");
        d(i[n - 1].toLowerCase())
    }

    function h() {
        var e = [];
        for (var r = 1; r < v(t, n) + 1; r++) {
            e.push({
                day: r,
                weekday: s[m(t, n, r)]
            })
        }
        return e
    }

    function p() {
        f.empty();
        for (var e = 0; e < 7; e++) {
            f.append("<div>" + s[e].substring(0, 3) + "</div>")
        }
    }

    function d(month) {
        var t;
        var n = $("#calendar").css("width", e + "px");
        var t = $('#calendar-container');
            t.removeClass();
            t.addClass(month);
        n.find(t = "#calendar_weekdays, #calendar_content").css("width", e + "px").find("div").css({
            width: e / 7 + "px",
            height: e / 7 + "px",
            "line-height": e / 7 + "px"
        });
        n.find("#calendar_header").css({
            height: e * (1 / 7) + "px"
        }).find('i[class^="icon-chevron"]').css("line-height", e * (1 / 7) + "px")
    }

    function v(e, t) {
        return (new Date(e, t, 0)).getDate()
    }

    function m(e, t, n) {
        return (new Date(e, t - 1, n)).getDay()
    }

    function g(e) {
        return y(new Date) == y(e)
    }

    function y(e) {
        return e.getFullYear() + "/" + (e.getMonth() + 1) + "/" + e.getDate()
    }

    function b() {
        var e = new Date;
        t = e.getFullYear();
        n = e.getMonth() + 1
    }
    var e = 280;
    var t = 2013;
    var n = 9;
    var r = [];
    var i = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
    var s = ["S", "M", "T", "W", "T", "F", "S"];
    var o = ["#16a085", "#1abc9c", "#c0392b", "#27ae60", "#FF6860", "#f39c12", "#f1c40f", "#e67e22", "#2ecc71", "#e74c3c", "#d35400", "#2c3e50"];
    var u = $("#calendar");
    var a = u.find("#calendar_header");
    var f = u.find("#calendar_weekdays");
    var l = u.find("#calendar_content");
    b();
    c();
    a.find('i[class^="icon-chevron"]').on("click", function() {
        var e = $(this);
        var r = function(e) {
            n = e == "next" ? n + 1 : n - 1;
            if (n < 1) {
                n = 12;
                t--
            } else if (n > 12) {
                n = 1;
                t++
            }
            c()
        };
        if (e.attr("class").indexOf("left") != -1) {
            r("previous")
        } else {
            r("next")
        }
    })

    // Expand calendar
    $('#pull-out-calendar header').on('click', function () {
        $(this).parent().toggleClass('expand');
    });

    // Reveal about
    $('a.about-button').on('click', function (event) {
        console.log('itt vagyok')
        event.preventDefault();
        $('#about').addClass('reveal');
        $('body').addClass('noscroll');
    });

    $('#close').on('click', function () {
        $('#about').removeClass('reveal');
        $('body').removeClass('noscroll');
    });

    // Flip cards
    $('article.date-card .card').on('click', function () {

        $(this).parent().toggleClass('flipped').siblings().removeClass('flipped');

        var extraMargin = 125;
        var number = this.parentElement.getAttribute("id").split("-")[1].toString();
        var scrollPoint =  $(this).offset().top - extraMargin;

        var scrollPoint =  $(this).offset().top - extraMargin;
        $('body,html').animate({
            scrollTop: scrollPoint
        }, 1200, 'swing');
        return false;
    });

    // Section H2 fix
    var januarysection = $('section#january').offset().top;

    $(window).scroll(function(){

        if( $(window).scrollTop() > januarysection ) {
			$('#pull-out-calendar').addClass('fixed');
            $('section#january').css({
                'box-shadow': 'inset 0 0 0 1000px rgba(240,164,66,0)'
            });
            $('#goToTop').css({
                'visibility':'visible',
                'opacity':1
            });
        } else {
            $('#pull-out-calendar').removeClass('fixed').removeClass('expand');
            $('section#january').css({
                'box-shadow': 'inset 0 0 0 1000px rgba(240,164,66,1)'
            });
            $('#goToTop').css({
                'visibility':'hidden',
                'opacity':0
            });
        }
    });

    if($(window).width() > 1200){

        // Push sticky header
        $('.feedify').feedify();
    }

	//mo
	bindTomorrowAndYesterday();
    $(".tomorrow button").click();
    $(".yesterday button").click();

    $("body").on("click", ".view-trailer", function() {
        var url = $(this).attr("data-trailer");
        $(this).css('padding',0);
		url = url.replace("https://youtu.be/", "https://www.youtube.com/embed/")
        if (url.indexOf("imdb") != -1) {
            $(this).html('<div class="videoWrapper"><iframe src="' + url + '" width="480" height="270" allowfullscreen="true" mozallowfullscreen="true" webkitallowfullscreen="true" frameborder="no" scrolling="no"></iframe></div>');
        } else if (url.trim() == "") {
            $(this).html('<div class="error" style="padding: 10px; text-align: center; font-size: 14px;">No trailer was found for this movie</div>');
        } else {
            $(this).html('<div class="videoWrapper"><iframe width="100%" height="315" src="' + url + '" frameborder="0" gesture="media" allowfullscreen></iframe></div>');
        }
    });

});

$(window).ready(function() {

    $('#main').on('click','.calendar-day', function() {

        $('article').removeClass('flipped');

        $('.calendar-day > div').removeClass('today-active');
        $(this).children().toggleClass('today-active');


        var extraMargin = 110,
            _topSection = $('div[data-anchor="top-section"]').offset().top - 10,
            _daysName = ["&lt;&lt; Yesterday", "Tomorrow &gt;&gt", "&lt;&lt; Previous", "Next &gt;&gt"],
            newSmallMonthDay = formatSmallMonthDay(),
            _dayShown = parseInt(newSmallMonthDay.split("-")[1]),
            scrollAnchor =  $(this).attr('data-scroll'),
            element = $('article[data-anchor="' + scrollAnchor + '"]')
            element.addClass('flipped'),
            dateInfo = element.attr("id").split("-"),
            dtCurrent.setDate(1);

            dtCurrent.setMonth(smallmonths.indexOf(dateInfo[0]));
            dtCurrent.setDate(parseInt(dateInfo[1]));
            var $movieContent = $("#" + element.attr("id") + " .expanded-movie").clone().append('<div class="yesterday"><button type="button">' +( _dayShown == _today ?_daysName[0] : _daysName[2] ) + '</button></div><div class="tomorrow"><button type="button">' + ( _dayShown == _today ?_daysName[1]:_daysName[3] ) + '</button></div><div class="clearfix"></div>');
            $movieContent.find("#close-icon").remove();
            $("div.watch-on-this-day .expanded-movie").replaceWith($movieContent);
            bindTomorrowAndYesterday();

        var scrollPoint =  $('article[data-anchor="' + scrollAnchor + '"]').offset().top - extraMargin;

        if($(window).width() < 768){
            $('html, body').animate({ scrollTop: _topSection }, 'slow');
            return false;
        }else{
            $('body,html').animate({
                scrollTop: scrollPoint
            }, 700, 'swing');
            return false;
        }
    });

    $('#goToTop').on('click', function(){
        $('html, body').animate({ scrollTop: 0 }, 'slow');
    });

});

}
/*
     FILE ARCHIVED ON 22:13:17 Mar 19, 2018 AND RETRIEVED FROM THE
     INTERNET ARCHIVE ON 12:16:52 Oct 28, 2024.
     JAVASCRIPT APPENDED BY WAYBACK MACHINE, COPYRIGHT INTERNET ARCHIVE.

     ALL OTHER CONTENT MAY ALSO BE PROTECTED BY COPYRIGHT (17 U.S.C.
     SECTION 108(a)(3)).
*/
/*
playback timings (ms):
  captures_list: 0.448
  exclusion.robots: 0.023
  exclusion.robots.policy: 0.014
  esindex: 0.008
  cdx.remote: 12.557
  LoadShardBlock: 366.872 (3)
  PetaboxLoader3.datanode: 222.365 (5)
  PetaboxLoader3.resolve: 174.201 (3)
  load_resource: 161.931 (2)
*/