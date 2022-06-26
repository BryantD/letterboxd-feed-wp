CREATE TABLE lb_feed (
	id TEXT PRIMARY KEY, 
	title TEXT, 
	ts TIMESTAMP, 
	link TEXT, 
	review TEXT, 
	year INT, 
	rating REAL, 
	spoilers INT, 
	UNIQUE(title, year)
);
