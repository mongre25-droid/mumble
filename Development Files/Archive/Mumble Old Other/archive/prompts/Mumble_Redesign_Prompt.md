# /mumble-redesign

So, I want you to read the core development files of Mumble, starting from the README, to really get a good understanding of what Mumble is. I then want you to run in undisturbed mode. You can use sub-agents if it's useful.

Now, what do I want you to fix?

## Homepage

On the homepage we have a "Listening Now" square. I want that removed, because it doesn't look good. I don't really like it. I think it's a waste of space, really. I just don't think it looks good, so I'd rather not have it there.

I also think the homepage needs to be redesigned. There's a large square that is just completely black. I feel like we need to better it — give it purpose, make it feel like it belongs.

## Smart Modes

If you look at Smart Modes, it says "Reply." Reply doesn't even exist. So what are we doing? It's just stupid. Absolutely stupid. Remove it.

## Deck

The Deck needs to be improved. First of all, it doesn't really maintain the colour scheme of Mumble towards the top. Yeah, it doesn't do it at all. It needs to feel like the rest of Mumble — the golden-black palette, the 3D liquid glass effect we have going on everywhere else.

## Stats

Stats also needs to be improved in a way where the first page basically has all the stats that someone would realistically need. Basically, I want it to be in a way where someone can screenshot that first page and it contains every single part of Mumble. So, the reader, meetings — any really good information to have. And then as you scroll down you've got more and more information, but less useful, right? So you want words per minute to be in there, total words, total hours saved — all to be at the front. Streak is fine, but there's no reader, no meeting stats in the widgets at the top. Basically, I want as much as we can pack into that first page.

Also, please remove the top bars in Stats where it says "Activity Dashboard" and explains what Stats is. Remove that. We already know what Stats is.

## Meetings

Meetings does not hold the same design language. It looks very black, and it doesn't have the 3D effects either — which is weird on that widget in the front and foremost part. It doesn't hold the same 3D liquid glass effect that we have going on on Mumble, even though it looks pretty nice in its own way.

And also, nothing is really centred — the top part is not really centred. Fix that.

And not only that, but Meetings as well has that same thing at the top where it says "Capture the conversation, keep the useful files," and the little subheading under it. We don't need all of that. Remove it.

## Search

Search is very underbaked. I'll show you what search should actually be like. I've seen an application where basically when you press the button, it opens up in a really small bar, and it holds all your best applications. I'm not talking weird files and weird text files — no, actual applications. And you can search for weird text files and all of that, but the most used stuff, the most important stuff, is there with the logo.

Whereas the search you've got right now is really weird the way it's structured. It doesn't look very good. It has the exact file location just under each result, which confuses the user. It has no logos. And also it is opening as an actual window instead of a pop-up, which doesn't make any sense. It should just be a pop-up that appears when you press Ctrl+Alt+F. That's it — a clean, fast launcher-style popup.

---

**The unifying thread through all of this:** Every surface should feel like the same application. The golden-black palette, the 3D liquid glass depth, the centred elegance. No redundant headers explaining what pages do. No features that don't exist. No dead empty black boxes. Just a tight, beautiful, cohesive experience.
