# Xanadu — relation explorer

An interactive 3D graph for exploring labelled relationships between things —
a mindmap / family-tree hybrid inspired by Ted Nelson's Xanadu. Click any node
to focus it and see how everything connects; drag to rotate through space.

It's a single self-contained HTML file. No server, no build step, no accounts —
everything runs in the browser and your data stays on your machine.

## Using it

- **Click** a card or connection to focus it.
- **Drag the background** to rotate; **drag a card** to reposition it.
- **Double-click** a card to edit it — name, description, group, image, links,
  and connections.
- **Zoom** with the control at the bottom-right (or scroll).
- **Load** opens a `.json` or `.zip` graph; **Merge** folds another graph into
  the current one, automatically combining duplicate nodes (matched by
  Wikipedia URL or exact name).
- **Export** saves the whole graph — as a plain `.json`, or a `.zip` when it
  contains uploaded images.

The graph that loads by default is a merged map of Greek myth, drawing together
the gods, the *Odyssey*, and the *Iliad* into one connected web.

## Building your own

Start from **New**, add nodes and connections, and Export when you're done.
Images can be pasted in as web URLs or uploaded from disk. A node can also link
to an entirely separate graph, which you can dive into and back out of — so a
single "interest" node might open onto a whole map of its own.
