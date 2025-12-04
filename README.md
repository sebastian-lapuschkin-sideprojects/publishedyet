# Has This been Published yet

This is a small pet project to help the whole group keep their bibtex collections clean. 
Repeating workflows during paper writing and revision are to check the bibtex entries for cited papers, in order to find out whether some of the cited arxiv preprints are by now published in conferences or journals. Since this workflow is repeated often, the idea behind this project is to build, maintain and automate an arxiv -> doi (or similar) data base.



## Milestones

- A first milestone is to enable the entering of an arxiv ID and to request from the dblp.org - API matching registered papers, and then search by authors or title for other entries which are peer reviewed
- Following steps include:
    + packaging all this functionality into a UI (grad.io?)
        + vertically list all papers we scan for. horizontally exend all candidate entries for one to pick per paper.
    + enable functionality to return (cleaned/sufficient/minimal) bibtex entries for chosen bibtex alternatives.
        + copypaste from browser vs download compiled/updated bibtex file (see later. replace entries? deliver completely new file? match existing bibtex keys?)
    + process bibtex entries
    + batch - process whole bibtex files or lists of arxiv identifiers
    + enable functionality to "approve" of identified entries,
        - based on this create an own database of pre-approved entries

## Requirements
    ?

## How to Run
    ?