# MacOS Setup (for programming novices)

## Packaged release

[Upstream packaged .app](https://github.com/Jcapehart2/RuneKit-Reforged/releases/tag/continuous)

This upstream build does not include this fork’s changes; macOS support remains experimental and untested.

## Running from source

This guide is assuming that you're willing install a few things and run a few commands in your `terminal` application.

Not sure how to open the terminal? You can try this [link](https://lmgtfy.app/?q=how+to+open+terminal+on+mac).

Got your terminal open? Great, time to run some commands.

### (1/4) Downloading this code

But first, we need to download this code. If you know how to use `git` to do that, great. If that last sentence made no sense, keep reading.

We're going to begin by downloading the code as a zip file. You can do this by clicking the green button on the GitHub repo page then clicking `Download Zip`, as shown below:

![download the code](./images/download-code.png)

Then, go ahead and open it with `Archive Utility`:

![unzip](./images/unzip.png)

You should now find the folder in your `Downloads` folder (use `Finder` to find it):

![downloads folder](./images/downloads-folder.png)

Great, we have the code! Next step please.

### (2/4) Installing Homebrew (a package manager)

Basically, follow the instructions on their (Homebrew's) website [here](https://brew.sh/).

Copy the command into your terminal prompt, then hit `enter`:

![install brew](./images/install-brew.png)

You can check that this worked by closing and reopening your terminal, then running `which brew`. You should get the following output:

![which brew](./images/which-brew.png)

Awesome, we'll need this tool later. For now, on to Python.

### (3/4) Installing Python (3.13)

Run the following command in your terminal:

```bash
brew install python@3.13
```

### (4/4) Installing poetry

Run the following command in your terminal:

```bash
brew install poetry
```

### One last thing...

Before you go and run the commands from the other page, you'll want to change what file directory your terminal is in to be the folder where you downloaded `RuneKit-Reforged-main`. Assuming you downloaded it as instructed in step 1, you can do this by running the following:

```bash
cd ~/Downloads/RuneKit-Reforged-main
```

**Note**: if you close and reopen the terminal, you'll need to renavigate to the correct folder (just rerun the above command).

That simple. Now you can go back to the other file and pick up where you left off, [here](../README.md#macos-quick-start-building-from-source).
