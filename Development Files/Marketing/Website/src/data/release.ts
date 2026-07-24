export const release = {
  version: '0.95',
  codename: 'The Big Shift',
  downloadPath: '/Mumble.zip',
  os: 'Windows 10 or later (64-bit)',
  ram: '4 GB minimum',
  disk: '~2 GB for the app, Python, and local model',
  runtime: 'Python is installed automatically during setup',
} as const;

export const product = {
  builtInPresets: 20,
  customPresets: 5,
  totalPresets: 25,
} as const;
