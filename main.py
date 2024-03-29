#!/usr/bin/python3

from dateutil.parser import parse

import signal
import time
import threading
import requests
import json
import pymongo
import datetime
import logging
import sys
from optparse import OptionParser


client = pymongo.MongoClient("localhost", 27017)
db = client['facebook']

pagesColl = db.pages
postsColl = db.posts
reactionsColl = db.reactions
commentsColl = db.comments


app_id = ''
app_secret = ''
token = ''

base_url = 'https://graph.facebook.com/v2.9/'

kill_now = False


logging.basicConfig(filename="error.log", level=logging.ERROR)
logger = logging.getLogger()


def unhandled_exception(exctype, value, tb):
    logger.error(tb)

sys.excepthook = unhandled_exception


class Scraper:
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        global kill_now
        kill_now = True


    def get_most_recent_date(self, page_id):
        regex = {'$regex': '^' + page_id}
        date = postsColl.count_documents({'_id': regex})
        print(date)
        #if date.count() == 0:
        if date == 0:
            print("No recent date found for page", page_id)
            return 'today'
        else:
            print("We have already extracted posts from page", page_id)
            recent_date = date[0]['created_time']
            recent_date += datetime.timedelta(seconds=1)
            return recent_date.isoformat()

    
    def get_oldest_date(self, page_id):
        regex = {'$regex': '^' + page_id}
        date = postsColl.count_documents({'_id': regex})

        #if date.count() == 0:
        if date == 0:
            print("No oldest date found for page", page_id)
            return 'today'
        else:
            print("We have already extracted posts from page", page_id)
            oldest_date = date[0]['created_time']
            oldest_date -= datetime.timedelta(seconds=1)
            return oldest_date.isoformat()

        
    def fetch_posts(self, page_id):
        since_date = '2021-01-01' # Por default buscar hasta enero del 2016
        now = datetime.datetime.utcnow()
        now += datetime.timedelta(days=1)
        until_date = now.isoformat()
        
        most_recent_date = self.get_most_recent_date(page_id)
        if most_recent_date != 'today':
            print("most recent date:", most_recent_date)
            self.fetch_posts_helper(page_id, most_recent_date, until_date, most_recent=True) 
            print("Finished extracting most recent posts for page", page_id)
        
        # Second, start extracting from the oldest known date
        # If we haven't extracted posts yet for thiss page, then start from today
        oldest_date = self.get_oldest_date(page_id)
        if oldest_date != 'today' and oldest_date != since_date:
            print("oldest date:", oldest_date)
            self.fetch_posts_helper(page_id, since_date, oldest_date)

        if most_recent_date == 'today' and oldest_date == 'today':
            self.fetch_posts_helper(page_id, since_date, until_date)


    def fetch_posts_helper(self, page_id, from_date, until_date, most_recent=False):
        global kill_now
        print("Fetching posts for page", page_id, "from", from_date, "until", until_date)

        request_url = base_url + page_id + '/posts?fields=created_time,message,name,description,shares,link&pretty=0&since=' + from_date + '&until=' + until_date + '&limit=100&access_token=' + token
        posts = requests.get(request_url).json()


        #first_time = True
        while True:
            try:
                #reactions = []
                comments = []
                for i, post in enumerate(posts['data']):
                    post['_id'] = post.pop('id', None)
                    post['created_time'] = parse(post['created_time'])
                    post['month'] = post['created_time'].month
                    post['year'] = post['created_time'].year
                    
                    if 'shares' in post:
                        post['shares'] = post['shares']['count']

                    print("post id:", post['_id'])
                    reactions = self.fetch_reactions(post['_id'])
                    post['angry'] = reactions['angry']
                    post['like'] = reactions['like']
                    post['haha'] = reactions['haha']
                    post['sad'] = reactions['sad']
                    post['love'] = reactions['love']
                    post['wow'] = reactions['wow']

                    comments.extend(self.fetch_comments(post['_id']))

                
                for post in posts['data']:
                    postsColl.update({'_id': post['_id']}, post, upsert=True)
                for comment in comments:
                    commentsColl.update({'_id': comment['_id']}, comment, upsert=True)
                
                if kill_now:
                    print("Exiting in 5 seconds...")
                    time.sleep(5)
                    return
                else:
                    posts = requests.get(posts['paging']['next']).json()

            except KeyError as e:

                print("Finished searching posts for page", page_id)
                return
            except pymongo.errors.BulkWriteError as bwe:
                logger.error(bwe.details)
                print("Bulk write error: ", bwe.details)
            except requests.exceptions.SSLError as ssle:
                logger.error(ssle)

        
    
    def fetch_reactions(self, post_id):
        request_url = base_url + post_id + '?fields=reactions.type(ANGRY).limit(0).summary(1).as(angry),reactions.type(HAHA).limit(0).summary(1).as(haha),reactions.type(LIKE).limit(0).summary(1).as(like),reactions.type(LOVE).limit(0).summary(1).as(love),reactions.type(SAD).limit(0).summary(1).as(sad),reactions.type(WOW).limit(0).summary(1).as(wow)&access_token=' + token
        reactions = requests.get(request_url).json()
        
        reactions['_id'] = reactions.pop('id', None)
        reactions['angry'] = reactions['angry']['summary']['total_count']
        reactions['like'] = reactions['like']['summary']['total_count']
        reactions['haha'] = reactions['haha']['summary']['total_count']
        reactions['sad'] = reactions['sad']['summary']['total_count']
        reactions['love'] = reactions['love']['summary']['total_count']
        reactions['wow'] = reactions['wow']['summary']['total_count']
        
        print("Finished searching reactions for post", post_id)
        return reactions



    def fetch_comments(self, post_id):
        request_url = base_url + post_id + '/comments?fields=created_time,message,id,like_count&limit=100&access_token=' + token

        comms = requests.get(request_url).json()
        comments = comms['data']
        
        while True:
            try:
                comms = requests.get(comms['paging']['next']).json()
                comments.extend(comms['data'])

            except KeyError:
                break
        
        for comment in comments:
            comment['_id'] = comment.pop('id', None)
            comment['created_time'] = parse(comment['created_time'])
            comment['month'] = comment['created_time'].month
            comment['year'] = comment['created_time'].year
        
        print("Finished fetching comments for post", post_id)
        return comments






if __name__ == '__main__':
    config_path = 'C:/Users/aso.RCTS/Downloads/facebook-api-scraping/'
    parser = OptionParser()
    parser.add_option("-f", "--file", dest="file", default=config_path+"config.json", help="name of the file with the configuration")
    (options, args) = parser.parse_args()
    print(options)
    with open(options.file) as config:
        data = json.load(config)
    
    app_id = data['credentials']['appId']
    app_secret = data['credentials']['appSecret']
    token = app_id + '|' + app_secret
    
    threads = []
    scrapers = []
    num_threads = len(data['pages'])    

    for i in range(len(data['pages'])):
        scrapers.append(Scraper())
        page_id = str(data['pages'][i]['id'])
        page_name = ''
        if 'name' in data['pages'][i]:
            page_name = data['pages'][i]['name']
        
        thread = threading.Thread(target=scrapers[i].fetch_posts, args=(page_id,))
        threads.append(thread)
        threads[i].start()
        print("Started thread for page", page_name, "-", page_id)

    for thread in threads:
        thread.join()



    date_index = 'created_time'
    if date_index not in postsColl.index_information():
        print("There is no 'created_time' index in 'posts' collection. Creating index now...")
        postsColl.create_index(date_index, name='created_time')
        print("Finished creating index for 'posts' collection")
    if date_index not in commentsColl.index_information():
        print("There is no 'created_time' index in 'comments' collection. Creating index now...")
        commentsColl.create_index(date_index, name='created_time')
        print("Finished creating index for 'comments' collection")

    print("End of the program. Killed gracefully.")

    #logger.close()
